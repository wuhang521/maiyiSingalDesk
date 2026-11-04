"""Qwen endpoints and bounded private-audio transcription; no automatic retry."""
import base64
import io
import json
import math
import re
import tempfile
import time
import wave
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from .config import get_settings
from .knowledge_extract import Extraction


def base_url():
    value = get_settings().knowledge_qwen_base_url.strip().rstrip('/')
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        official = host in {'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com'} or re.fullmatch(
            r'[a-z0-9-]{1,64}\.(cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com', host)
        if official and url.scheme == 'https' and url.port in (None, 443) and not (url.username or url.password or url.query or url.fragment) and url.path == '/compatible-mode/v1':
            return value
    except ValueError:
        pass
    return ''


def configured():
    return bool(base_url() and get_settings().knowledge_qwen_api_key.strip())


def recognize(path, filename, *, audio_preparer, may_send, transport=None):
    from .knowledge_asr import AsrError
    settings = get_settings()
    if not configured() or not re.fullmatch(r'qwen3-asr-flash(?:-\d{4}-\d{2}-\d{2})?', settings.knowledge_cloud_asr_model.strip()):
        raise AsrError('千问语音转写模型或百炼凭据尚未配置；原件保留。')
    deadline = time.monotonic() + 600
    texts, chunks = [], []
    try:
        with tempfile.TemporaryDirectory(prefix='signaldesk-qwen-asr-') as directory:
            audio = Path(directory) / 'audio.wav'
            duration = audio_preparer(path, filename, audio)
            with wave.open(str(audio), 'rb') as source, httpx.Client(
                headers={'Authorization': 'Bearer ' + settings.knowledge_qwen_api_key.strip()},
                timeout=httpx.Timeout(90, connect=5), follow_redirects=False, trust_env=False, transport=transport,
            ) as client:
                rate, total = source.getframerate(), source.getnframes()
                if source.getnchannels() != 1 or source.getsampwidth() != 2 or rate != 16000 or not math.isfinite(duration) or not 0 < duration <= settings.knowledge_cloud_asr_max_minutes * 60 or abs(total / rate - duration) > 1:
                    raise ValueError('invalid converted audio')
                # 180 s PCM is < 8 MB after Base64; no public URL or source video.
                segment_frames = rate * 180
                for start in range(0, total, segment_frames):
                    if not may_send():
                        raise AsrError('账号、资料或任务已改变，未继续发送音轨；已发送部分可能计费。')
                    if time.monotonic() > deadline:
                        raise AsrError('千问转写时间预算用尽，未采用不完整结果，未自动重试。')
                    pcm = source.readframes(min(segment_frames, total - start))
                    if len(pcm) != min(segment_frames, total - start) * 2:
                        raise ValueError('truncated converted audio')
                    encoded = io.BytesIO()
                    with wave.open(encoded, 'wb') as segment:
                        segment.setnchannels(1); segment.setsampwidth(2); segment.setframerate(rate); segment.writeframes(pcm)
                    body = dict(model=settings.knowledge_cloud_asr_model.strip(), stream=False,
                                messages=[dict(role='user', content=[dict(type='input_audio', input_audio={
                                    'data': 'data:audio/wav;base64,' + base64.b64encode(encoded.getvalue()).decode('ascii')})])],
                                asr_options={'enable_itn': False})
                    with client.stream('POST', base_url() + '/chat/completions', json=body) as response:
                        from .knowledge_provider_trace import record
                        if response.status_code != 200:
                            record('qwen', response)
                            raise AsrError('千问转写请求失败，未自动重试；请检查模型权限和额度。')
                        raw = bytearray()
                        for block in response.iter_bytes():
                            raw.extend(block)
                            if len(raw) > 2_000_000 or time.monotonic() > deadline:
                                raise ValueError('response exceeded budget')
                        payload = json.loads(raw); record('qwen', response, payload)
                    choice = payload['choices'][0]
                    text = choice['message']['content']
                    if choice.get('finish_reason') != 'stop' or not isinstance(text, str) or not text.strip() or len(text) > 100000:
                        raise ValueError('incomplete transcription')
                    text = text.strip(); texts.append(text)
                    locator = f'{start / rate:.2f}s–{min(total, start + segment_frames) / rate:.2f}s 音轨片段（千问 AI 转写，非逐句时间轴）'
                    chunks.extend((locator, text[i:i + 900]) for i in range(0, len(text), 900))
                if not may_send() or not chunks or sum(map(len, texts)) > 300000:
                    raise AsrError('转写结果过期、为空或超限，未采用；原件已保留。')
        return Extraction('\n'.join(texts), 'qwen_asr_draft', chunks)
    except AsrError:
        raise
    except (httpx.HTTPError, ValueError, OSError, wave.Error, KeyError, TypeError, IndexError, ZeroDivisionError):
        raise AsrError('千问转写结果未确认，未自动重试；原件保留，再次识别可能再次计费。') from None
