"""Synthetic-only Qwen wire, privacy, batching and confirmation regressions."""
import base64
import io
import json
import wave

import httpx
import pytest

from app import knowledge_asr, knowledge_qwen, knowledge_semantic
from app.config import get_settings
from app.knowledge_worker import claim, finish
from test_knowledge import kb, collect


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def qwen(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, 'knowledge_qwen_api_key', 'synthetic-qwen-key')
    monkeypatch.setattr(settings, 'knowledge_qwen_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setattr(settings, 'knowledge_cloud_asr_provider', 'qwen')
    monkeypatch.setattr(settings, 'knowledge_cloud_asr_model', 'qwen3-asr-flash')
    monkeypatch.setattr(settings, 'knowledge_cloud_asr_enabled', True)
    monkeypatch.setattr(settings, 'knowledge_embedding_provider', 'qwen')
    monkeypatch.setattr(settings, 'knowledge_embedding_model', 'text-embedding-v4')
    monkeypatch.setattr(settings, 'knowledge_semantic_enabled', True)
    monkeypatch.setattr(knowledge_asr.shutil, 'which', lambda _: 'synthetic-executable')


def audio(seconds):
    def prepare(_path, _name, output):
        with wave.open(str(output), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(b'\0\0' * int(seconds * 16000))
        return seconds
    return prepare


def test_qwen_asr_sends_only_bounded_audio_and_uses_honest_segment_locators(tmp_path, monkeypatch):
    qwen(monkeypatch); calls = []
    def respond(request):
        body = json.loads(request.content); calls.append(body)
        assert request.url.path == '/compatible-mode/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer synthetic-qwen-key'
        assert body['model'] == 'qwen3-asr-flash' and body['asr_options']['enable_itn'] is False
        encoded = body['messages'][0]['content'][0]['input_audio']['data']
        assert encoded.startswith('data:audio/wav;base64,')
        with wave.open(io.BytesIO(base64.b64decode(encoded.split(',', 1)[1]))) as stream:
            assert stream.getnframes() / stream.getframerate() <= 180
        assert not any(key in body for key in ['source_url', 'filename', 'user_id'])
        return httpx.Response(200, json={'id': 'synthetic-request', 'choices': [{'finish_reason': 'stop', 'message': {'content': '合成转写第' + str(len(calls)) + '段'}}]})
    result = knowledge_asr.recognize(tmp_path / 'unused.mp4', 'private.mp4', 'synthetic-job', transport=httpx.MockTransport(respond), audio_preparer=audio(181))
    assert result.method == 'qwen_asr_draft' and len(calls) == 2
    assert '0.00s–180.00s' in result.chunks[0][0] and '非逐句时间轴' in result.chunks[0][0]
    assert '180.00s–181.00s' in result.chunks[1][0]


def test_qwen_asr_revocation_stops_later_segments_and_provider_failure_never_retries(tmp_path, monkeypatch):
    qwen(monkeypatch); calls = []
    def respond(_request):
        calls.append(1)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '合成结果'}}]})
    with pytest.raises(knowledge_asr.AsrError, match='改变'):
        knowledge_asr.recognize(tmp_path / 'unused.mp4', 'sample.mp4', 'job', transport=httpx.MockTransport(respond), audio_preparer=audio(181), may_send=lambda: not calls)
    assert len(calls) == 1
    calls.clear()
    def failure(_request): calls.append(1); return httpx.Response(503, json={'error': 'synthetic failure'})
    with pytest.raises(knowledge_asr.AsrError, match='未自动重试'):
        knowledge_asr.recognize(tmp_path / 'unused.wav', 'sample.wav', 'job', transport=httpx.MockTransport(failure), audio_preparer=audio(1))
    assert len(calls) == 1


@pytest.mark.anyio
async def test_qwen_embeddings_split_to_ten_and_preserve_order(monkeypatch):
    qwen(monkeypatch); batches = []
    def respond(request):
        body = json.loads(request.content); batches.append(body['input'])
        assert request.url.host == 'dashscope.aliyuncs.com'
        assert len(body['input']) <= 10
        return httpx.Response(200, json={'model': 'text-embedding-v4', 'data': [
            {'index': index, 'embedding': [float(int(text) + 1), 1.0]} for index, text in reversed(list(enumerate(body['input'])))
        ]})
    vectors, model = await knowledge_semantic.embed([str(i) for i in range(23)], transport=httpx.MockTransport(respond))
    assert list(map(len, batches)) == [10, 10, 3] and model == 'text-embedding-v4'
    assert len(vectors) == 23 and vectors[0][0] < vectors[-1][0]


@pytest.mark.anyio
async def test_qwen_embedding_revocation_stops_next_batch(monkeypatch):
    qwen(monkeypatch); calls = []
    def respond(request):
        body = json.loads(request.content); calls.append(1)
        return httpx.Response(200, json={'model': 'text-embedding-v4', 'data': [{'index': i, 'embedding': [1, 2]} for i in range(len(body['input']))]})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await knowledge_semantic.embed(['text'] * 11, transport=httpx.MockTransport(respond), may_send=lambda: not calls)
    assert error.value.status_code == 409 and len(calls) == 1


def test_qwen_asr_queue_confirm_is_private_and_not_searchable_before_review(kb, monkeypatch):
    qwen(monkeypatch)
    client, users, factory, _ = kb
    item = collect(client, users[0], title='千问测试原件')
    item = client.post(f"/api/v1/knowledge/items/{item['id']}/assets", params={'filename': 'synthetic.wav', 'revision': item['revision']}, headers={**users[0], 'Content-Type': 'application/octet-stream'}, content=b'RIFF-synthetic').json()
    job = claim(factory)
    finish(job, dict(state='needs_input', note='合成原件等待主动识别'), factory)
    item = client.get(f"/api/v1/knowledge/items/{item['id']}", headers=users[0]).json()
    queued = client.post(f"/api/v1/knowledge/items/{item['id']}/assets/{item['assets'][0]['id']}/asr", headers=users[0], json={'revision': item['revision'], 'cloud_consent': True})
    assert queued.status_code == 200
    job = claim(factory); assert job['request']['mode'] == 'qwen_asr'
    finish(job, dict(state='ready', method='qwen_asr_draft', text='千问合成转写材料', chunks=[('0–1s 音轨片段', '千问合成转写材料')]), factory)
    assert not client.post('/api/v1/knowledge/ask', headers=users[0], json={'question': '千问合成'}).json()['citations']
    current = client.get(f"/api/v1/knowledge/items/{item['id']}", headers=users[0]).json()
    endpoint = f"/api/v1/knowledge/items/{item['id']}/asr/confirm"
    assert client.post(endpoint, headers=users[1], json={'revision': current['revision'], 'confirmation': '已核对'}).status_code == 404
    ready = client.post(endpoint, headers=users[0], json={'revision': current['revision'], 'confirmation': '已核对'})
    assert ready.status_code == 200 and ready.json()['method'] == 'qwen_asr_reviewed'
    assert client.post('/api/v1/knowledge/ask', headers=users[0], json={'question': '千问合成'}).json()['citations']


@pytest.mark.parametrize('url', ['https://dashscope.aliyuncs.com.attacker.test/compatible-mode/v1', 'http://dashscope.aliyuncs.com/compatible-mode/v1', 'https://dashscope.aliyuncs.com/compatible-mode/v1?token=x', 'https://user:pass@dashscope.aliyuncs.com/compatible-mode/v1'])
def test_qwen_rejects_nonofficial_or_credential_bearing_endpoints(monkeypatch, url):
    qwen(monkeypatch); monkeypatch.setattr(get_settings(), 'knowledge_qwen_base_url', url)
    assert not knowledge_qwen.configured() and not knowledge_asr.configuration() and not knowledge_semantic.configuration()
