"""Read-only deployment inventory. Never print env values or account contents."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

APP = Path('/opt/signaldesk-task')
sys.path.insert(0, str(APP)); os.chdir(APP)
def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
units = {}
for unit in ['signaldesk-task-api.service', 'signaldesk-task-worker.service', 'signaldesk-knowledge-worker.service']:
    raw = run(['systemctl', 'cat', unit])
    units[unit] = {'active': run(['systemctl', 'is-active', unit]).strip(),
                   'safe_fields': [line for line in raw.splitlines() if line.startswith(('User=', 'Group=', 'WorkingDirectory=', 'EnvironmentFile='))]}
files = ['app/main.py', 'app/config.py', 'requirements.txt', 'migrations/env.py', 'app/knowledge.py']
hashes = {f: hashlib.sha256((APP/f).read_bytes()).hexdigest() if (APP/f).is_file() else None for f in files}
from app.config import get_settings
from sqlalchemy import create_engine, text
s = get_settings()
engine = create_engine(s.database_url)
with engine.connect() as conn:
    revision = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
    counts = {t: conn.execute(text('SELECT COUNT(*) FROM '+t)).scalar() for t in ['staff','user_account','task','task_event','notification_outbox']}
envfiles = [APP/'.env', Path('/home/hermes/.hermes/.env')]
envmeta = {}
for f in envfiles:
    values = dict(line.split('=', 1) for line in f.read_text().splitlines() if '=' in line and not line.lstrip().startswith('#'))
    envmeta[str(f)] = {key: bool(values.get(key, '').strip().strip('"\'')) for key in ['TASK_KNOWLEDGE_GATEWAY_SECRET','TASK_KNOWLEDGE_OCR_API_KEY','TASK_API_BASE_URL','TASK_API_KEY','ARK_API_KEY']}
root = Path('/home/hermes/.hermes/plugins')
plugins = sorted(p.name for p in root.iterdir() if p.is_dir())
java = []
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit(): continue
    try:
        argv = (proc/'cmdline').read_bytes().split(b'\0')
        jars = [a.decode() for a in argv if a.endswith(b'.jar') and a.startswith(b'/')]
        for jar in jars:
            info = {'pid': int(proc.name), 'jar': jar, 'doubao_configuration_entries': []}
            try:
                with zipfile.ZipFile(jar) as archive:
                    for name in archive.namelist():
                        if name.endswith(('.yml','.yaml')) and '/classes/' in name:
                            body = archive.read(name).decode()
                            if 'doubao:' in body:
                                info['doubao_configuration_entries'].append(name)
            except Exception: pass
            java.append(info)
    except (OSError, UnicodeError): pass
runtime = '''
from pathlib import Path
import json, os, importlib.util
profiles=[]
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit(): continue
    try:
        pairs=dict(p.split(b'=',1) for p in (proc/'environ').read_bytes().split(b'\\0') if b'=' in p)
        if b'HERMES_HOME' in pairs:
            profiles.append({'pid':proc.name,'home':pairs[b'HERMES_HOME'].decode()})
    except OSError: pass
print(json.dumps({'profiles':profiles,'pypdf':bool(importlib.util.find_spec('pypdf'))}))
for base in ['hermes_cli/plugins.py', 'hermes_plugins/__init__.py']:
    f=Path('/opt/hermes')/base
    if f.exists():
        print(base)
        for line in f.read_text().splitlines():
            if any(s in line for s in ['import ', 'def ', 'enabled', 'HERMES_HOME', 'plugins_dir']): print(line)
'''
result = subprocess.run(['docker','exec','-i','hermes','/opt/hermes/.venv/bin/python','-'], input=runtime,capture_output=True,text=True,timeout=10)
print(json.dumps({'units': units,'hashes': hashes,'revision':revision,'counts':counts,'env_presence':envmeta,'plugins':plugins,'java':java,'runtime':result.stdout}, ensure_ascii=False))
