"""Read-only main-instance integration topology; never prints keys or message data."""
import json
from pathlib import Path
from urllib.parse import urlsplit

path = Path('/opt/signaldesk-task/.env')
settings = {}
if path.is_file():
    for line in path.read_text(encoding='utf-8').splitlines():
        key, separator, value = line.partition('=')
        if separator: settings[key.strip()] = value.strip().strip('"').strip("'")
url = urlsplit(settings.get('TASK_HERMES_WEBHOOK_URL', ''))
print(json.dumps(dict(webhook=dict(scheme=url.scheme, host=url.hostname, port=url.port, path=url.path),
    dedicated_asr_configured=bool(settings.get('TASK_KNOWLEDGE_CLOUD_ASR_API_KEY') or
        settings.get('TASK_KNOWLEDGE_CLOUD_ASR_APP_ID') and settings.get('TASK_KNOWLEDGE_CLOUD_ASR_ACCESS_KEY')),
    embedding_configured=bool(settings.get('TASK_KNOWLEDGE_EMBEDDING_API_KEY') and settings.get('TASK_KNOWLEDGE_EMBEDDING_MODEL')),
    config_exists=path.is_file()), ensure_ascii=False))
