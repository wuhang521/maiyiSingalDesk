"""Exercise the applied health API without sending messages or writing articles."""
import hashlib,json,subprocess
import httpx
from app.channel_reconcile import capture,fingerprints
from app.config import get_settings
from app.database import SessionLocal

with SessionLocal() as db:before=fingerprints(capture(db))
result={'messages_sent':0,'article_or_personal_state_writes_requested':False}
headers={'X-SignalDesk-Key':get_settings().api_key}
with httpx.Client(base_url='http://127.0.0.1:19080',headers=headers,timeout=20,trust_env=False) as client:
 channels=client.get('/api/v1/channels');channels.raise_for_status()
 health=[item for item in channels.json() if item['id']=='health'];assert len(health)==1
 channel=health[0];assert channel['managed'] and channel['readViaApi'] and channel['state']=='active' and channel['schedule']=='09:00'
 assert 'demo-health' in channel['aliases'] and not any(item['id']=='demo-health' for item in channels.json())
 original=hashlib.sha256(json.dumps(channel,sort_keys=True).encode()).hexdigest()
 statuses=[]
 for identity in ['demo-health','health']:
  response=client.post('/api/v1/channels',json={'id':identity,'name':'obsolete cache acceptance','topic':'','state':'draft'})
  statuses.append(response.status_code);assert response.status_code==200 and response.json()['id']=='health'
 updated=next(item for item in client.get('/api/v1/channels').json() if item['id']=='health')
 assert hashlib.sha256(json.dumps(updated,sort_keys=True).encode()).hexdigest()==original
 result.update(single_canonical_health=True,managed=True,read_via_api=True,schedule=channel['schedule'],max_items=channel['maxItems'],obsolete_cache_posts=statuses,channel_configuration_unchanged=True)
 unauth=httpx.get('http://127.0.0.1:19080/api/v1/health-relay/health/json?poll=1&since=10m',timeout=10,trust_env=False)
 assert unauth.status_code==401;result['unauthenticated_relay_status']=unauth.status_code
 poll=client.get('/api/v1/health-relay/health/json',params={'poll':'1','since':'10m'})
 if not poll.is_success:
  result.update(passed=False,failed_stage='actual_relay_poll',http_status=poll.status_code)
  print(json.dumps(result));raise SystemExit(1)
 cached=[json.loads(line) for line in poll.text.splitlines() if line.strip()]
 assert all(item['topic']=='health' for item in cached)
 result['actual_relay_poll']={'status':poll.status_code,'events':len(cached),'cache_control':poll.headers.get('cache-control')}
 with client.stream('GET','/api/v1/health-relay/health/json',params={'since':'10m'}) as stream:
  stream.raise_for_status();first=json.loads(next(stream.iter_lines()))
  assert first['topic']=='health' and first['event'] in {'open','keepalive','message'}
  result['actual_relay_stream']={'status':stream.status_code,'first_event':first['event'],'canonical_topic':True,'upstream_buffering_disabled':stream.headers.get('x-accel-buffering')=='no','closed_after_first_event':True}
with SessionLocal() as db:after=fingerprints(capture(db))
assert before==after;result['fingerprints']=after;result['all_article_and_personal_states_unchanged']=True
states={}
for unit in ['signaldesk-task-api','signaldesk-task-worker','signaldesk-knowledge-worker','signaldesk-knowledge-dispatch','signaldesk-worker']:
 response=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=10)
 states[unit]=response.stdout.strip()
assert all(value=='active' for value in states.values());result['existing_service_states']=states
result['passed']=True
print(json.dumps(result))
