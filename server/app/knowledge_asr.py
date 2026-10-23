"""Explicit, single-submit Volcengine speech transcription of private local media.

Official contract: https://www.volcengine.com/docs/6561/1631584?lang=zh
Conversion is local, bounded and shell-free; only the resulting audio leaves host.
"""
import base64
import importlib.util
import json
import math
from pathlib import Path
import shutil
import os
import sys
import subprocess
import tempfile
import time
import uuid
import wave

import httpx

from .config import get_settings
from .knowledge_extract import Extraction, MEDIA_EXTENSIONS

ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
RESOURCE = "volc.bigasr.auc_turbo"
MAX_AUDIO_BYTES = 20 * 1024 * 1024


class AsrError(Exception):
    """Messages safe for the user; never return provider responses/keys/paths."""


def configuration():
    s = get_settings()
    credentials = s.knowledge_cloud_asr_api_key.strip() or (
        s.knowledge_cloud_asr_app_id.strip() and s.knowledge_cloud_asr_access_key.strip())
    return bool(s.knowledge_cloud_asr_enabled and credentials and
                shutil.which(s.knowledge_ffmpeg_binary) and shutil.which(s.knowledge_ffprobe_binary))


def silk_configuration():
    try: return bool(get_settings().knowledge_silk_enabled and importlib.util.find_spec("pilk"))
    except (ValueError, ImportError): return False


def local_command(args, *, timeout):
    try:
        # No URLs, playlists, remote protocols, shell or ambient credentials.
        return subprocess.run(args, timeout=timeout, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, shell=False).stdout
    except (subprocess.SubprocessError, OSError):
        raise AsrError("音视频检查或本地转换失败，未提交云端；请检查文件或联系管理员。") from None


def prepare_audio(path, filename, output):
    s = get_settings()
    if Path(filename).suffix.lower() not in MEDIA_EXTENSIONS or not path.is_file():
        raise AsrError("请选择已保存的音频或视频文件。")
    if not 0 < path.stat().st_size <= s.knowledge_max_upload_mb * 1024 * 1024:
        raise AsrError("音视频超过后台上传上限，请拆分后导入。")
    if Path(filename).suffix.lower() == ".silk":
        if not silk_configuration() or path.stat().st_size > 20 * 1024 * 1024:
            raise AsrError("微信原生语音已保留，但本地 SILK 解码尚未启用或原件超过 20 MB；请上传 WAV / MP3，不自动安装解码器。")
        decoded = output.parent / "decoded-silk.wav"
        safe_env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
        try:
            subprocess.run([sys.executable, "-m", "app.knowledge_silk", str(path.resolve()), str(decoded.resolve())],
                cwd=Path(__file__).resolve().parents[1], env=safe_env, timeout=45, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        except (OSError, subprocess.SubprocessError): raise AsrError("微信语音本地解码失败，未发送云端；原件保留，请转为 WAV / MP3。") from None
        path = decoded
    probe = local_command([s.knowledge_ffprobe_binary, "-v", "error", "-protocol_whitelist", "file,pipe",
                           "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path.resolve())], timeout=15)
    try:
        if len(probe) > 1_000_000:
            raise ValueError()
        metadata = json.loads(probe)
        duration = float(metadata["format"]["duration"])
        if not math.isfinite(duration) or not 0 < duration <= s.knowledge_cloud_asr_max_minutes * 60:
            raise ValueError()
        if not any(stream.get("codec_type") == "audio" for stream in metadata["streams"]):
            raise AsrError("素材没有可识别音轨，未发送云端；可补充字幕或图片。")
    except (ValueError, KeyError, TypeError):
        raise AsrError(f"音视频时长无法确认或超过 {s.knowledge_cloud_asr_max_minutes} 分钟；请拆分，未发送云端。") from None
    # One extra second makes a dishonest input duration detectable, not silently truncated.
    local_command([s.knowledge_ffmpeg_binary, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                   "-i", str(path.resolve()), "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000",
                   "-t", str(s.knowledge_cloud_asr_max_minutes * 60 + 1), "-c:a", "pcm_s16le",
                   "-f", "wav", "-n", str(output)], timeout=90)
    try:
        if not 44 < output.stat().st_size <= MAX_AUDIO_BYTES:
            raise ValueError()
        with wave.open(str(output), "rb") as audio:
            actual_duration = audio.getnframes() / audio.getframerate()
            if audio.getnchannels() != 1 or audio.getframerate() != 16000 or audio.getsampwidth() != 2:
                raise ValueError()
        if not 0 < actual_duration <= s.knowledge_cloud_asr_max_minutes * 60:
            raise ValueError()
        if abs(duration - actual_duration) > 2:
            raise ValueError()
    except (OSError, ValueError, wave.Error, ZeroDivisionError):
        raise AsrError("音轨时长、格式或完整性检查失败；未提交云端，请拆分或重新导出素材。") from None
    return actual_duration


def decode_result(payload, duration):
    try:
        result = payload["result"]
        overall = result["text"]
        segments = result["utterances"]
        reported = payload["audio_info"]["duration"]
        if type(reported) not in (int, float) or not math.isfinite(reported) or abs(reported / 1000 - duration) > 2:
            raise ValueError()
        if not isinstance(overall, str) or not overall.strip() or len(overall) > 100_000:
            raise ValueError()
        if not isinstance(segments, list) or not 1 <= len(segments) <= 5000:
            raise ValueError()
        chunks, previous_start, texts = [], -1, []
        for segment in segments:
            start, end, text = segment["start_time"], segment["end_time"], segment["text"]
            if type(start) is not int or type(end) is not int or not previous_start <= start <= end <= round(duration * 1000) + 1000:
                raise ValueError()
            if start < 0 or not isinstance(text, str) or len(text) > 10_000:
                raise ValueError()
            previous_start = start
            text = text.strip()
            if text:
                texts.append(text)
                locator = f"{start / 1000:.2f}s–{end / 1000:.2f}s（AI 转写）"
                chunks.extend((locator, text[i:i + 900]) for i in range(0, len(text), 900))
        # Reject missing/mismatched sentence coverage rather than cite incomplete text.
        if "".join(overall.split()) != "".join("".join(texts).split()) or not chunks:
            raise ValueError()
    except (ValueError, TypeError, KeyError, OverflowError):
        raise AsrError("云端转写未返回完整且匹配的正文和时间轴，未入库；请补充字幕或联系管理员。") from None
    return Extraction("\n".join(texts), "volc_asr_draft", chunks)


def recognize(path, filename, request_id, *, transport=None, audio_preparer=prepare_audio, may_send=lambda: True):
    if not configuration():
        raise AsrError("云端语音识别未启用、缺少独立 ASR 凭据或转换工具；原件已保留。")
    s = get_settings()
    # Persistent job ID is generated before any remote call, not regenerated on recovery.
    request_id = str(uuid.UUID(request_id))
    headers = {"X-Api-Resource-Id": RESOURCE, "X-Api-Request-Id": request_id, "X-Api-Sequence": "-1"}
    if s.knowledge_cloud_asr_api_key.strip():
        headers["X-Api-Key"] = s.knowledge_cloud_asr_api_key.strip()
    else:
        headers.update({"X-Api-App-Key": s.knowledge_cloud_asr_app_id.strip(),
                        "X-Api-Access-Key": s.knowledge_cloud_asr_access_key.strip()})
    with tempfile.TemporaryDirectory(prefix="signaldesk-cloud-asr-") as directory:
        audio = Path(directory) / "audio.wav"
        duration = audio_preparer(path, filename, audio)
        if not may_send():
            raise AsrError("本地转换期间账号、资料或任务改变，未提交云端；原件已保留。")
        data = dict(user={"uid": "signaldesk-knowledge"}, audio={"data": base64.b64encode(audio.read_bytes()).decode("ascii")},
                    request={"model_name": "bigmodel", "enable_itn": True, "enable_punc": True, "show_utterances": True})
        deadline = time.monotonic() + 200
        try:
            with httpx.Client(headers=headers, follow_redirects=False, trust_env=False, transport=transport,
                              timeout=httpx.Timeout(180, connect=5, write=30, pool=5)) as client:
                with client.stream("POST", ENDPOINT, json=data) as response:
                    if response.status_code != 200 or response.headers.get("X-Api-Status-Code") != "20000000":
                        from .knowledge_provider_trace import record
                        record("volc_asr", response)
                        raise AsrError("云端转写请求失败，未自动重试；请检查独立 ASR 权限或额度。再次识别可能再次计费。")
                    body = bytearray()
                    for block in response.iter_bytes():
                        body.extend(block)
                        if len(body) > 2_000_000 or time.monotonic() > deadline:
                            raise AsrError("云端转写结果超限或超时，结果未确认；未自动重试。")
                    payload = json.loads(body)
                    from .knowledge_provider_trace import record
                    record("volc_asr", response, payload)
            return decode_result(payload, duration)
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise AsrError("云端转写结果未能确认，未自动重试；原件已保留，再次识别可能再次计费。") from None
