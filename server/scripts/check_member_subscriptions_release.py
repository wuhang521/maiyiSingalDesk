"""Exercise the real app on a disposable database; never use production settings."""
from datetime import timedelta
import json

from fastapi.testclient import TestClient
from app.auth import token_digest
from app.database import SessionLocal
from app.main import app
from app.models import SmartChannel, Staff, StaffChannel, UserAccount, UserSession, utcnow

BASE = '/api/v1/channel-subscriptions'
ADMIN = {'Authorization': 'Bearer isolated-release-admin'}
MEMBER = {'Authorization': 'Bearer isolated-release-member'}

with TestClient(app) as client:
    with SessionLocal.begin() as db:
        db.add_all([Staff(id=1, name='Fixture admin'), Staff(id=2, name='Fixture member')])
        db.add_all([UserAccount(id=1, staff_id=1, username='release-admin', role='admin', password_hash='fixture'),
                    UserAccount(id=2, staff_id=2, username='release-member', password_hash='fixture')])
        db.add_all(UserSession(user_id=n, token_hash=token_digest(headers['Authorization'].split()[1]),
                              expires_at=utcnow() + timedelta(minutes=5))
                   for n, headers in enumerate((ADMIN, MEMBER), start=1))
        db.add(StaffChannel(staff_id=2, platform='weixin', recipient_id='isolated-release-peer'))
        db.add_all([SmartChannel(id='a', name='A', state='active'), SmartChannel(id='b', name='B', state='active')])

    route = f'{BASE}/members/2'
    state = client.get(route, headers=ADMIN).json()
    def draft(selected, snapshot=state):
        return dict(previous_channel_ids=snapshot['subscribed_channel_ids'], channel_ids=selected,
                    binding_revision=snapshot['binding_revision'])
    assert client.get(route).status_code == 401
    assert client.get(route, headers=MEMBER).status_code == 403
    assert client.put(route, headers=MEMBER, json=draft(['a'])).status_code == 403
    enabled = client.put(route, headers=ADMIN, json=draft(['a', 'b']))
    assert enabled.status_code == 200 and enabled.json()['subscribed_channel_ids'] == ['a', 'b']
    assert all(row['subscribed'] for row in client.get(f'{BASE}/catalog', headers=MEMBER).json()['channels'])
    assert client.put(route, headers=ADMIN, json=draft([])).status_code == 409
    invalid = draft(['a'], enabled.json()); invalid['binding_revision'] = '0' * 64
    assert client.put(route, headers=ADMIN, json=invalid).status_code == 409
    cancelled = client.put(route, headers=ADMIN, json=draft([], enabled.json()))
    assert cancelled.status_code == 200 and cancelled.json()['subscribed_channel_ids'] == []
    assert not any(row['subscribed'] for row in client.get(f'{BASE}/catalog', headers=MEMBER).json()['channels'])
    assert client.get('/api/v1/admin/members', headers=ADMIN).status_code == 200
print(json.dumps({'passed': True, 'database': 'isolated SQLite', 'production_subscription_writes': 0,
                  'checks': ['admin enable', 'member observes', 'admin cancel', 'member denied',
                             'stale snapshot conflict', 'changed binding conflict', 'member API retained']}))
