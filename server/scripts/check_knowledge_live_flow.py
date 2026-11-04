"""Real provider / real worker acceptance with a NEW synthetic SQLite database."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

checks = []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--audio', required=True, type=Path)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--extended-only', action='store_true')
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    if candidate.parent != Path('/var/tmp') or not candidate.name.startswith('signaldesk-knowledge-candidate-'):
        raise ValueError('Expected isolated candidate')
    from dotenv import dotenv_values
    hermes = dotenv_values('/home/hermes/.hermes/.env')
    production = dotenv_values('/opt/signaldesk-task/.env')
    os.environ['TASK_DATABASE_URL'] = 'sqlite:///' + str(candidate / ('live-synthetic-' + uuid.uuid4().hex + '.db'))
    os.environ['TASK_KNOWLEDGE_STORAGE_DIR'] = str(candidate / ('live-synthetic-files-' + uuid.uuid4().hex))
    os.environ['TASK_API_KEY'] = 'synthetic-live-flow'
    os.environ['TASK_KNOWLEDGE_QWEN_API_KEY'] = hermes['DASHSCOPE_API_KEY']
    os.environ['TASK_KNOWLEDGE_CLOUD_ASR_PROVIDER'] = 'qwen'
    os.environ['TASK_KNOWLEDGE_CLOUD_ASR_ENABLED'] = 'true'
    os.environ['TASK_KNOWLEDGE_EMBEDDING_PROVIDER'] = 'qwen'
    os.environ['TASK_KNOWLEDGE_EMBEDDING_MODEL'] = 'text-embedding-v4'
    os.environ['TASK_KNOWLEDGE_SEMANTIC_ENABLED'] = 'true'
    os.environ['TASK_KNOWLEDGE_OCR_ENABLED'] = 'true'
    os.environ['TASK_KNOWLEDGE_OCR_API_KEY'] = production['TASK_KNOWLEDGE_OCR_API_KEY']
    if production.get('TASK_KNOWLEDGE_OCR_MODEL'):
        os.environ['TASK_KNOWLEDGE_OCR_MODEL'] = production['TASK_KNOWLEDGE_OCR_MODEL']
    os.chdir(candidate); sys.path.insert(0, str(candidate))
    from app.main import app
    from app.database import Base, engine, SessionLocal
    from app.models import Staff, UserAccount
    from app.auth import create_user_session
    from app.knowledge_worker import run_once
    from app.knowledge_models import KnowledgeCloudUsage, KnowledgeProviderTrace
    from fastapi.testclient import TestClient
    from sqlalchemy import select, func
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        user = UserAccount(username='synthetic-live-flow', staff=Staff(name='Synthetic live-provider tester'), role='member', password_hash='not-used')
        db.add(user); db.flush(); token, _ = create_user_session(db, user); db.commit()
    headers = {'Authorization': 'Bearer ' + token}
    with TestClient(app) as client:
        def get(item): return client.get('/api/v1/knowledge/items/' + item['id'], headers=headers).json()
        def post(url, data):
            response = client.post('/api/v1/knowledge' + url, headers=headers, json=data)
            if response.status_code not in {200, 201}: raise RuntimeError('API acceptance failed at ' + url.rsplit('/', 1)[-1] + ': ' + str(response.status_code))
            return response.json()
        def upload(file):
            item = post('/items', {'title': 'Synthetic real-provider ' + file.name})
            response = client.post('/api/v1/knowledge/items/' + item['id'] + '/assets', params={'filename': file.name, 'revision': item['revision']}, headers={**headers, 'Content-Type': 'application/octet-stream'}, content=file.read_bytes())
            assert response.status_code == 200
            assert run_once()
            return get(item)
        if args.extended_only:
            extended(args, production, post, upload, get, run_once)
            return
        item = upload(args.audio)
        queued = post('/items/' + item['id'] + '/assets/' + item['assets'][0]['id'] + '/asr', {'revision': item['revision'], 'cloud_consent': True})
        assert run_once()
        draft = get(queued)
        assert draft['status'] == 'needs_review' and draft['method'] == 'qwen_asr_draft'
        assert 'knowledge base integration test' in draft['content'].lower()
        assert not post('/ask', {'question': 'knowledge base'})['citations']
        ready = post('/items/' + item['id'] + '/asr/confirm', {'revision': draft['revision'], 'confirmation': '已核对'})
        assert post('/ask', {'question': 'knowledge base'})['citations']
        checks.append('real Qwen ASR: upload → worker → private draft → owner review → cited excerpt')
        index = post('/items/' + item['id'] + '/semantic-index', {'revision': ready['revision'], 'cloud_consent': True})
        assert index['state'] == 'ready'
        time.sleep(15.1)  # Exercise the actual per-owner admission interval.
        answer = post('/semantic-search', {'question': 'How should sources be checked?', 'cloud_consent': True})
        assert any(row['item_id'] == item['id'] for row in answer['citations'])
        checks.append('real Qwen index and query vectors → current original citation')
        image = upload(args.image)
        queued = post('/items/' + image['id'] + '/assets/' + image['assets'][0]['id'] + '/ocr', {'revision': image['revision'], 'cloud_consent': True})
        assert run_once()
        draft = get(queued)
        assert draft['status'] == 'needs_review' and 'KNOWLEDGE' in draft['content'].upper() and 'SOURCES' in draft['content'].upper()
        assert not post('/ask', {'question': 'SOURCES BEFORE QUOTING'})['citations']
        post('/items/' + image['id'] + '/ocr/confirm', {'revision': draft['revision'], 'confirmation': '已核对'})
        assert post('/ask', {'question': 'SOURCES BEFORE QUOTING'})['citations']
        checks.append('real OCR: synthetic PNG → worker → reviewed page citation')
        with SessionLocal() as db:
            submissions = db.scalar(select(func.count()).select_from(KnowledgeCloudUsage))
            trace_count = db.scalar(select(func.count()).select_from(KnowledgeProviderTrace))
        assert submissions == 4 and trace_count >= 4
        print(json.dumps(dict(passed=True, checks=checks, submissions=submissions, provider_trace_rows=trace_count,
                             production_changed=False, notification_sent=False, material='Synthetic local speech and text-only PNG'), ensure_ascii=False, indent=2))


def extended(args, production, post, upload, get, run_once):
    import subprocess
    from app.config import get_settings
    settings = get_settings()
    settings.knowledge_video_ocr_enabled = True
    settings.knowledge_video_ocr_max_frames = 2
    settings.knowledge_cloud_qa_enabled = True
    settings.knowledge_organization_enabled = True
    settings.prompt_api_key = production.get('TASK_PROMPT_API_KEY', '')
    settings.prompt_model = production.get('TASK_PROMPT_MODEL', '')
    settings.prompt_base_url = production.get('TASK_PROMPT_BASE_URL', '')
    video = args.candidate / ('synthetic-video-' + uuid.uuid4().hex + '.mp4')
    subprocess.run([settings.knowledge_ffmpeg_binary, '-nostdin', '-v', 'error', '-loop', '1', '-i', str(args.image),
                    '-i', str(args.audio), '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'stillimage',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-r', '12', '-shortest', '-n', str(video)],
                   check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    item = upload(video)
    post('/items/' + item['id'] + '/assets/' + item['assets'][0]['id'] + '/asr', {'revision': item['revision'], 'cloud_consent': True})
    assert run_once(); item = get(item)
    assert item['status'] == 'needs_review' and 'knowledge base integration test' in item['content'].lower()
    post('/items/' + item['id'] + '/assets/' + item['assets'][0]['id'] + '/video-ocr', {'revision': item['revision'], 'cloud_consent': True})
    assert run_once(); item = get(item)
    assert item['status'] == 'needs_review' and 'SOURCES' in item['content'].upper()
    # Local source parts must correspond to this same uploaded synthetic video.
    from app.database import SessionLocal
    from app.knowledge_models import KnowledgeMediaPart
    from sqlalchemy import select
    with SessionLocal() as db:
        parts = db.scalars(select(KnowledgeMediaPart).where(KnowledgeMediaPart.item_id == item['id'])).all()
        speech = next(part.id for part in parts if part.method == 'qwen_asr_draft')
        frames = next(part.id for part in parts if part.method == 'video_ocr_draft')
    merged = post('/items/' + item['id'] + '/media-merge', {'revision': item['revision'], 'speech_id': speech, 'frames_id': frames, 'acknowledge_draft': True})
    ready = post('/items/' + item['id'] + '/media-merge/confirm', {'revision': merged['revision'], 'confirmation': '已核对'})
    checks.append('real synthetic video → Qwen audio + sampled OCR → source-matched reviewed merge')
    notes = post('/items/' + item['id'] + '/organization', {'revision': ready['revision'], 'cloud_consent': True})
    assert notes['state'] == 'draft' and notes['payload']['summary']['citations']
    assert get(item)['content'] == ready['content']
    checks.append('real configured text model → cited AI notes without changing source')
    time.sleep(15.1)
    answer = post('/ask', {'question': 'How should sources be saved before quoting?', 'mode': 'cloud', 'cloud_consent': True})
    assert answer['mode'] == 'cloud' and answer['citations']
    checks.append('real configured text model → answer with original citations')
    print(json.dumps(dict(passed=True, checks=checks, production_changed=False, notification_sent=False,
                         material='Synthetic PNG and local speech combined into a video; configured text provider retained.'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(passed=False, checks=checks, error_type=type(error).__name__, detail=str(error) if isinstance(error, AssertionError) else 'Inspect isolated synthetic stage; credentials and provider bodies are omitted.'), ensure_ascii=False))
        raise SystemExit(1)
