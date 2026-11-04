"""Read-only deployment readiness. Run via SSH stdin; never print key values."""
import json
from pathlib import Path
import shutil
import subprocess

root = Path('/opt/signaldesk-task')
values = {}
env_file = root / '.env'
if env_file.is_file():
    for line in env_file.read_text().splitlines():
        if line.lstrip().startswith('#'): continue
        key, separator, value = line.partition('=')
        if separator: values[key.strip()] = value.strip().strip('"').strip("'")
fields = ['TASK_PROMPT_API_KEY', 'TASK_PROMPT_MODEL', 'TASK_KNOWLEDGE_OCR_API_KEY',
          'TASK_KNOWLEDGE_CLOUD_ASR_API_KEY', 'TASK_KNOWLEDGE_CLOUD_ASR_APP_ID',
          'TASK_KNOWLEDGE_CLOUD_ASR_ACCESS_KEY', 'TASK_KNOWLEDGE_EMBEDDING_API_KEY',
          'TASK_KNOWLEDGE_EMBEDDING_MODEL', 'TASK_KNOWLEDGE_GATEWAY_SECRET',
          'TASK_KNOWLEDGE_NOTIFICATION_WEBHOOK_SECRET']
flags = {key: value.lower() == 'true' for key, value in values.items()
         if key.startswith('TASK_KNOWLEDGE_') and key.endswith('_ENABLED')}
services = {}
for name in ['signaldesk-task-api', 'signaldesk-task-worker', 'signaldesk-knowledge-worker', 'signaldesk-knowledge-dispatch']:
    result = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True, timeout=10)
    services[name] = result.stdout.strip()
python = next((p for p in [root / '.venv/bin/python', root / 'venv/bin/python'] if p.is_file()), None)
schema = None
if python:
    result = subprocess.run([str(python), '-c', 'from app.database import engine; from sqlalchemy import text; c=engine.connect(); print(c.execute(text("SELECT version_num FROM alembic_version")).scalar()); c.close()'],
                            cwd=root, capture_output=True, text=True, timeout=15)
    schema = result.stdout.strip() if result.returncode == 0 else 'read_failed'
qwen_sources = {}
for candidate in [env_file, Path('/home/hermes/.hermes/.env'), Path('/home/hermes/.hermes/profiles/main/.env'), Path('/opt/data/.env')]:
    if not candidate.is_file(): continue
    key_present = False
    for line in candidate.read_text().splitlines():
        name, _, value = line.partition('=')
        if name.strip() == 'DASHSCOPE_API_KEY':
            key_present = bool(value.strip().strip('"').strip("'") and not value.strip().startswith('${'))
    qwen_sources[str(candidate)] = key_present
print(json.dumps(dict(env_exists=env_file.is_file(), configured={key: bool(values.get(key)) for key in fields}, qwen_sources=qwen_sources,
                      enabled=flags, services=services, schema=schema,
                      tools={name: bool(shutil.which(name)) for name in ['ffmpeg', 'ffprobe']},
                      note='Read-only presence check; no inference, messages, migration or restart.'), ensure_ascii=False, indent=2))
