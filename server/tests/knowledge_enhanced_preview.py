"""Synthetic-provider adapters for actual Electron/API/worker integration tests."""
import functools
import json
from pathlib import Path
import sys
import wave

import httpx
from fastapi import Body
from sqlalchemy import select


def install(app):
    from app import knowledge_asr, knowledge_cloud, knowledge_semantic, knowledge_video_ocr
    from app.config import get_settings
    from app.database import SessionLocal
    from app.auth import hash_password
    from app.models import Staff, UserAccount
    from app import knowledge_ocr
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == 'preview-kb'))
        user.role = 'admin'
        db.add(UserAccount(username='preview-colleague', staff=Staff(name='合成协作者'), role='member', password_hash=hash_password('preview-only-password')))
        db.commit()
    counts = {'asr': 0, 'embeddings': 0, 'organization': 0}

    def qwen_response(request):
        body = json.loads(request.content)
        if request.url.path.endswith('/embeddings'):
            counts['embeddings'] += 1
            return httpx.Response(200, json={'model': body['model'], 'data': [
                {'index': index, 'embedding': [1.0, float('健康' in text), 0.1]} for index, text in enumerate(body['input'])]})
        counts['asr'] += 1
        return httpx.Response(200, json={'id': 'synthetic-qwen-call', 'choices': [{'finish_reason': 'stop', 'message': {'content': '软件测试合成转写：健康管理归档应保存来源并核对正文。这不是真实模型识别结果。'}}]})

    def cloud_response(request):
        body = json.loads(request.content)
        if '"summary"' in body['messages'][0]['content']:
            counts['organization'] += 1
            result = {'summary': {'text': '软件测试模拟摘要：归档时核对资料来源。', 'citations': [1]},
                      'points': [{'text': '保留当前正文版本。', 'citations': [1]}], 'tags': ['合成整理标签']}
        else:
            result = {'insufficient': False, 'statements': [{'text': '软件测试模拟综合：请核对原文。', 'citations': [1]}]}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result, ensure_ascii=False)}}]})

    def synthetic_audio(_path, _filename, output):
        with wave.open(str(output), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000); stream.writeframes(b'\0\0' * 16000)
        return 1.0

    def synthetic_frames(_path, _filename, directory):
        import base64
        frame = directory / 'frame.png'
        frame.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1cAAAAASUVORK5CYII='))
        return [(0.0, frame)]

    original_asr, original_embed, original_video = knowledge_asr.recognize, knowledge_semantic.embed, knowledge_video_ocr.recognize
    knowledge_asr.recognize = functools.partial(original_asr, transport=httpx.MockTransport(qwen_response), audio_preparer=synthetic_audio)
    knowledge_semantic.embed = functools.partial(original_embed, transport=httpx.MockTransport(qwen_response))
    knowledge_video_ocr.recognize = functools.partial(original_video, frame_extractor=synthetic_frames, image_recognizer=knowledge_ocr.recognize)

    @app.post('/preview-enhanced/enable')
    def enable():
        settings = get_settings()
        settings.knowledge_cloud_asr_provider = 'qwen'
        settings.knowledge_cloud_asr_enabled = True
        settings.knowledge_cloud_asr_model = 'qwen3-asr-flash'
        settings.knowledge_qwen_api_key = 'synthetic-provider-key'
        settings.knowledge_qwen_base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        settings.knowledge_semantic_enabled = True
        settings.knowledge_embedding_provider = 'qwen'
        settings.knowledge_embedding_model = 'text-embedding-v4'
        settings.knowledge_organization_enabled = True
        settings.knowledge_video_ocr_enabled = True
        settings.knowledge_ffmpeg_binary = sys.executable
        settings.knowledge_ffprobe_binary = sys.executable
        settings.prompt_api_key, settings.prompt_model, settings.prompt_base_url = 'synthetic-model-key', 'synthetic-text-model', 'https://api.deepseek.com'
        settings.knowledge_paid_daily_limit, settings.knowledge_paid_monthly_limit = 200, 1000
        knowledge_cloud.cloud_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(cloud_response), trust_env=False, follow_redirects=False)
        knowledge_cloud.admission = knowledge_cloud.Admission()
        knowledge_semantic.admission = knowledge_cloud.Admission()
        return {'enabled': True, 'provider': 'synthetic only', 'counts': counts}

    @app.get('/preview-enhanced/counts')
    def read_counts(): return counts
