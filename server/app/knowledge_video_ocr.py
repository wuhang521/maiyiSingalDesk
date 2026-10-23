"""Sparse frame transcription; never claims complete video or audio coverage."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import time

from .config import get_settings
from .knowledge_asr import AsrError, local_command
from .knowledge_extract import Extraction
from . import knowledge_ocr

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


def configuration():
    s=get_settings()
    return bool(s.knowledge_video_ocr_enabled and knowledge_ocr.configuration() and
                shutil.which(s.knowledge_ffmpeg_binary) and shutil.which(s.knowledge_ffprobe_binary))


def frames(path, filename, directory):
    s=get_settings()
    if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
        raise knowledge_ocr.OcrError("请选择已保存的视频；分享链接不能直接识别画面。")
    try:
        raw=local_command([s.knowledge_ffprobe_binary,"-v","error","-protocol_whitelist","file,pipe",
            "-show_entries","format=duration:stream=codec_type","-of","json",str(path.resolve())],timeout=15)
        if len(raw)>1_000_000: raise ValueError()
        metadata=json.loads(raw); duration=float(metadata['format']['duration'])
        if not math.isfinite(duration) or not 0<duration<=600:
            raise ValueError()
        if not any(stream.get('codec_type')=='video' for stream in metadata['streams']):
            raise ValueError()
        outputs=[]; seen=set()
        for index in range(s.knowledge_video_ocr_max_frames):
            at=min(max(0,duration-.1),index*duration/max(1,s.knowledge_video_ocr_max_frames-1))
            output=directory/f'frame-{index}.png'
            local_command([s.knowledge_ffmpeg_binary,'-nostdin','-v','error','-protocol_whitelist','file,pipe',
                '-ss',f'{at:.3f}','-i',str(path.resolve()),'-map','0:v:0','-an','-sn','-dn','-frames:v','1',
                '-vf',"scale=w='min(1600,iw)':h='min(1600,ih)':force_original_aspect_ratio=decrease",'-n',str(output)],timeout=20)
            if not output.is_file() or not 8<output.stat().st_size<=20*1024*1024: raise ValueError()
            digest=hashlib.sha256(output.read_bytes()).hexdigest()
            if digest in seen: continue
            seen.add(digest); outputs.append((at,output))
        if not outputs: raise ValueError()
        return outputs
    except (AsrError,ValueError,KeyError,TypeError,OSError):
        raise knowledge_ocr.OcrError("本地画面采样失败或视频超过 10 分钟；未发送云端，请检查原件或拆分。") from None


def recognize(path, filename, may_send, *, frame_extractor=frames, image_recognizer=knowledge_ocr.recognize):
    if not configuration():
        raise knowledge_ocr.OcrError("视频画面 OCR 尚未启用、缺少 OCR 凭据或转换工具；原件已保留。")
    with tempfile.TemporaryDirectory(prefix='signaldesk-video-ocr-') as directory:
        samples=frame_extractor(path,filename,Path(directory))
        deadline=time.monotonic()+600
        texts=[]; chunks=[]
        for at,frame in samples:
            if time.monotonic()>deadline:
                raise knowledge_ocr.OcrError("画面 OCR 时间预算用尽，未采用不完整结果；已停止后续调用，不自动重试。")
            if not may_send():
                raise knowledge_ocr.OcrError("识别期间账号、资料或任务改变，已停止后续画面请求；当前调用可能已经计费。")
            # These are ephemeral local PNGs, not provider uploaded files.
            result=image_recognizer(frame,'frame.png',lambda _: None,allow_empty=True,may_send=may_send)
            if not result.text.strip(): continue
            locator=f'{at:.2f}s 采样画面（AI OCR）'
            texts.append(f'[{locator}]\n{result.text}')
            chunks.extend((locator,result.text[i:i+900]) for i in range(0,len(result.text),900))
        body='视频画面稀疏采样文字，非完整视频转录；未识别音轨。\n\n'+'\n\n'.join(texts)
        if not texts or len(body)>300_000:
            raise knowledge_ocr.OcrError("采样画面没有可识别文字或结果超限；原件已保留，不自动再次识别。")
        return Extraction(body,'video_ocr_draft',chunks)
