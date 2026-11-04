"""Bounded live private gateway verification, without sending a real chat message."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

APP = Path('/opt/signaldesk-task')
os.chdir(APP); sys.path.insert(0, str(APP))
from app.database import SessionLocal
from app.models import Staff, StaffChannel, UserAccount
from sqlalchemy import select

with SessionLocal() as db:
    row = db.execute(select(StaffChannel.platform, StaffChannel.recipient_id).join(Staff).join(UserAccount, UserAccount.staff_id == Staff.id).where(
        StaffChannel.enabled.is_(True), Staff.enabled.is_(True), Staff.removed_at.is_(None), UserAccount.enabled.is_(True),
        StaffChannel.platform.in_(['qq','weixin','wecom']))).first()
if not row:
    print(json.dumps({'bound_private_sender_test': False, 'reason': 'No active bound user; requires administrator channel binding'}))
    sys.exit(2)
context = dict(platform=row.platform, sender_id=row.recipient_id, value='kb-deployment-no-evidence-' + uuid.uuid4().hex)
code = '''
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
from hermes_cli.plugins import get_plugin_manager, get_plugin_command_handler
manager=get_plugin_manager(); manager.discover_and_load()
handler=get_plugin_command_handler('wechat-knowledge-result')
assert callable(handler), 'Personal KB command is not registered'
module=sys.modules[handler.__module__]
assert Path('/opt/data/signaldesk-knowledge.env').is_file() and os.access('/opt/data/signaldesk-knowledge.env',os.R_OK)
context=CONTEXT
source=SimpleNamespace(platform=context['platform'],user_id=context['sender_id'],chat_type='dm')
event=SimpleNamespace(source=source,text='我的知识库搜索：'+context['value'])
rewritten=module.pre_gateway_dispatch(event=event,gateway=None)
assert rewritten and rewritten['action']=='rewrite'
answer=asyncio.run(handler(rewritten['text'].split(' ',1)[1]))
assert '没有找到已识别的相关正文' in answer, 'Bound sender did not reach personal KB'
assert module.pre_gateway_dispatch(event=SimpleNamespace(source=source,text='知识库搜索：'+context['value']),gateway=None) is None
group=module.dispatch(dict(platform=context['platform'],sender_id=context['sender_id'],chat_type='group',operation='search',value=context['value']))
assert '只接受私聊' in group
unknown=module.dispatch(dict(platform=context['platform'],sender_id=context['value'],chat_type='dm',operation='search',value=context['value']))
assert '绑定' in unknown or '开通' in unknown
plugins=manager.list_plugins()
personal=[p for p in plugins if p.get('name')=='wechat-knowledge']
native=[p for p in plugins if p.get('name')=='knowledge-base']
assert personal and personal[0]['enabled'], 'Personal KB plugin disabled'
assert native and native[0]['enabled'], 'Native KB plugin changed'
print(json.dumps(dict(personal_plugin_loaded=True,native_plugin_preserved=True,private_signed_gateway=True,
    unbound_sender_rejected=True,group_private_data_blocked=True,native_command_not_intercepted=True,
    real_chat_message_sent=False,paid_ocr_called=False)))
'''.replace('CONTEXT', repr(context))
result = subprocess.run(['docker','exec','-i','-e','HERMES_HOME=/opt/data','hermes','/opt/hermes/.venv/bin/python','-'],
                        input=code, capture_output=True, text=True, timeout=45)
stage = Path('/var/tmp/signaldesk-knowledge-20261008')
if result.returncode:
    (stage / 'gateway-verification.private.log').write_text(result.stdout + '\n' + result.stderr)
    print(json.dumps({'verified': False, 'diagnostic': 'server-local gateway-verification.private.log'}))
    sys.exit(1)
lines = result.stdout.splitlines()
safe = next((json.loads(line) for line in reversed(lines) if line.startswith('{') and 'personal_plugin_loaded' in line), None)
assert safe
(stage / 'gateway-verification.json').write_text(json.dumps(safe, indent=2))
print(json.dumps(safe))
