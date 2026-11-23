"""Disposable HTTP backend for the real Hermes-runtime onboarding probe."""
import ipaddress
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
database = os.environ.get('TASK_DATABASE_URL', '')
if not database.startswith('sqlite:///') or not database.endswith('/onboarding-e2e.db'):
    raise RuntimeError('A disposable onboarding-e2e.db is required')
if Path(database.removeprefix('sqlite:///')).exists():
    raise RuntimeError('Refusing to reuse a preview database')
host, port = sys.argv[1], int(sys.argv[2])
if not ipaddress.ip_address(host).is_private:
    raise RuntimeError('Preview must bind to a local/private test interface')

from app.main import app
from app.database import Base, SessionLocal, engine
from app.models import SmartChannel, Staff, StaffChannel, UserAccount
from app.channel_subscription_models import ChannelSubscription
from app.channel_subscriptions import binding_fingerprint
import uvicorn

Base.metadata.create_all(engine)
with SessionLocal.begin() as db:
    for identifier, name in [(1, 'fixture-a'), (2, 'fixture-b')]:
        db.add(UserAccount(id=identifier, staff=Staff(id=identifier, name=name), username=name,
                           role='member', password_hash='fixture'))
        db.add(StaffChannel(id=identifier, staff_id=identifier, platform='weixin',
                           recipient_id=name + '@im.wechat', wechat_profile=name))
    db.add_all([SmartChannel(id='ai', name='AI 决策雷达', state='active', schedule='09:00', subscription_user_ids=[1]),
                SmartChannel(id='medical', name='医疗前沿频道', state='active', schedule='09:00'),
                SmartChannel(id='admin-existing', name='管理员已有订阅', state='paused', schedule='09:00')])
    db.flush()
    binding = db.get(StaffChannel, 1)
    db.add(ChannelSubscription(user_id=1, channel_id='admin-existing', binding_id=1,
                               binding_fingerprint=binding_fingerprint(binding)))

uvicorn.run(app, host=host, port=port, log_level='warning')
