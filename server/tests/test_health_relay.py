"""Synthetic SQLite/auth API + actual HTTPX async NDJSON stream acceptance."""
import asyncio
import json
import os
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault('TASK_API_KEY','test-key')
from app import articles,auth,health_relay
from app.channel_aliases import SmartChannelAlias
from app.database import Base,get_db
from app.models import Article,ArticleUserState,SmartChannel


class Frames(httpx.AsyncByteStream):
    def __init__(self,chunks):self.chunks=chunks;self.closed=False
    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk,Exception):raise chunk
            yield chunk
    async def aclose(self):self.closed=True


def frame(event='message',**extra):
    value={'id':'synthetic-id','time':1786400000,'event':event,'topic':'synthetic-private-topic'}
    if event=='message':value.update(title='Synthetic title',message='Synthetic body',tags=['business'],priority=3)
    value.update(extra)
    return json.dumps(value,ensure_ascii=False).encode()+b'\n'


@pytest.fixture
def relay_api(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'relay.sqlite'),connect_args={'check_same_thread':False})
    class TrackedSession(Session):
        released=False
        def close(self):self.released=True;super().close()
    factory=sessionmaker(engine,class_=TrackedSession,autoflush=False,expire_on_commit=False)
    Base.metadata.create_all(engine)
    sessions=[]
    def database():
        with factory() as db:
            sessions.append(db)
            yield db
    with factory.begin() as db:
        db.add(SmartChannel(id='health',name='Synthetic managed',server='https://ntfy.synthetic.test',
                            topic='synthetic-private-topic',state='active'))
        db.add(SmartChannel(id='other',name='Synthetic other'))
        db.add(SmartChannelAlias(alias_id='demo-health',canonical_id='health'))
        db.add(SmartChannelAlias(alias_id='legacy-other',canonical_id='other'))
    config=tmp_path/'synthetic-private.env'
    config.write_text('NTFY_SERVER=https://ntfy.synthetic.test\nHEALTH_NTFY_TOPIC=synthetic-private-topic\nNTFY_TOKEN=synthetic-server-token\n',encoding='utf-8')
    config.chmod(0o600)
    monkeypatch.setattr(health_relay,'CONFIG_PATH',config)
    monkeypatch.setattr(auth,'get_settings',lambda:SimpleNamespace(api_key='synthetic-api-key'))
    app=FastAPI();app.include_router(articles.router);app.dependency_overrides[get_db]=database
    clients=[];requests=[];streams=[]
    def upstream(chunks=None,status=200,content_type='application/x-ndjson',error=None):
        async def handle(request):
            assert sessions[-1].released,'DB session must close before any upstream I/O'
            requests.append(request)
            if error:raise error
            stream=Frames(chunks if chunks is not None else [frame('open'),frame()])
            streams.append(stream)
            return httpx.Response(status,headers={'content-type':content_type,'location':'https://never-follow.example.test'},stream=stream)
        def client():
            assert sessions[-1].released
            value=httpx.AsyncClient(transport=httpx.MockTransport(handle),follow_redirects=False,trust_env=False)
            clients.append(value);return value
        monkeypatch.setattr(health_relay,'new_upstream_client',client)
    upstream()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client,headers={'X-SignalDesk-Key':'synthetic-api-key'},factory=factory,
                              config=config,sessions=sessions,upstream=upstream,clients=clients,
                              requests=requests,streams=streams,monkeypatch=monkeypatch)
    engine.dispose()


def get(api,query='?poll=1&since=12h',headers=None):
    return api.client.get('/api/v1/health-relay/health/json'+query,headers=headers or api.headers)


def test_auth_fixed_route_read_only_and_channel_contract(relay_api):
    api=relay_api
    assert api.client.get('/api/v1/health-relay/health/json').status_code==401
    assert api.client.get('/api/v1/health-relay/other/json',headers=api.headers).status_code==404
    assert api.client.post('/api/v1/health-relay/health/json',headers=api.headers).status_code==405
    rows={r['id']:r for r in api.client.get('/api/v1/channels',headers=api.headers).json()}
    assert rows['health']['readViaApi'] is True
    assert rows['other']['managed'] is True and rows['other']['readViaApi'] is False


def test_fixed_target_credential_isolation_poll_parse_and_no_database_writes(relay_api):
    api=relay_api
    response=get(api,'?poll=1&since=24h&server=https://evil.example.test&topic=other',
                 headers={**api.headers,'Authorization':'Bearer synthetic-client-token','Cookie':'synthetic-cookie'})
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store' and response.headers['x-accel-buffering']=='no'
    assert response.headers['content-type'].startswith('application/x-ndjson')
    events=[json.loads(line) for line in response.text.splitlines()]
    assert [event['event'] for event in events]==['open','message']
    assert all(event['topic']=='health' for event in events)
    assert 'synthetic-private-topic' not in response.text
    request=api.requests[0]
    assert str(request.url).startswith('https://ntfy.synthetic.test/synthetic-private-topic/json?')
    assert request.url.params['poll']=='1' and request.url.params['since']=='24h'
    assert request.headers['authorization']=='Bearer synthetic-server-token'
    assert 'x-signaldesk-key' not in request.headers and 'cookie' not in request.headers
    assert 'synthetic-client-token' not in str(request.headers)
    assert all(c.is_closed for c in api.clients) and all(s.closed for s in api.streams)
    with api.factory() as db:
        assert not db.scalars(select(Article)).all() and not db.scalars(select(ArticleUserState)).all()
        assert db.get(SmartChannel,'health').topic=='synthetic-private-topic'


@pytest.mark.parametrize('since',['10m','1h','6h','12h','24h','1d'])
def test_bounded_history_durations_are_accepted(relay_api,since):
    relay_api.upstream(chunks=[])
    response=get(relay_api,'?poll=1&since='+since)
    assert response.status_code==200 and response.content==b''
    assert relay_api.requests[0].url.params['since']==since


@pytest.mark.parametrize('query',['?poll=2&since=10m','?poll=-1','?since=all','?since=3d','?since=7d','?since=999999999999999999999','?since=https://evil.test'])
def test_invalid_parameters_never_open_upstream(relay_api,query):
    assert get(relay_api,query).status_code==422
    assert not relay_api.requests and not relay_api.clients


@pytest.mark.parametrize('failure',['no-file','not-managed','server-mismatch','topic-mismatch','http-upstream'])
def test_config_and_database_must_match_before_upstream(relay_api,failure):
    api=relay_api
    if failure=='no-file':api.config.unlink()
    elif failure=='http-upstream':
        api.config.write_text('NTFY_SERVER=http://ntfy.synthetic.test\nHEALTH_NTFY_TOPIC=synthetic-private-topic\n',encoding='utf-8')
        with api.factory.begin() as db:db.get(SmartChannel,'health').server='http://ntfy.synthetic.test'
    else:
        with api.factory.begin() as db:
            if failure=='not-managed':db.delete(db.get(SmartChannelAlias,'demo-health'))
            elif failure=='server-mismatch':db.get(SmartChannel,'health').server='https://different.synthetic.test'
            else:db.get(SmartChannel,'health').topic='different-synthetic-topic'
    response=get(api)
    assert response.status_code==503 and not api.requests and not api.clients
    assert 'synthetic' not in response.text and api.sessions[-1].released


@pytest.mark.parametrize('status,ctype',[(302,'application/x-ndjson'),(401,'application/x-ndjson'),(500,'application/x-ndjson'),(200,'text/html')])
def test_upstream_error_and_redirect_do_not_become_connected(relay_api,status,ctype):
    api=relay_api;api.upstream(chunks=[b'NEVER_REFLECT_PRIVATE_UPSTREAM_BODY'],status=status,content_type=ctype)
    response=get(api)
    assert response.status_code==502 and len(api.requests)==1
    assert 'PRIVATE' not in response.text and 'http' not in response.text
    assert all(s.closed for s in api.streams) and all(c.is_closed for c in api.clients)


@pytest.mark.parametrize('chunks',[[b'not-json\n'],[frame('unknown')],[frame(topic='other-private-topic')],
                                   [b'\xff\n'],[frame()+b'broken-later-json\n'],[b'x'*(64*1024+1)]])
def test_bad_poll_json_unknown_events_and_oversize_lines_fail_closed(relay_api,chunks):
    api=relay_api;api.upstream(chunks=chunks)
    response=get(api)
    assert response.status_code==502 and 'broken' not in response.text and 'private' not in response.text
    assert all(s.closed for s in api.streams) and all(c.is_closed for c in api.clients)


def test_poll_total_and_event_count_limits(relay_api):
    api=relay_api;api.monkeypatch.setattr(health_relay,'MAX_POLL_BYTES',64)
    assert get(api).status_code==502
    api.monkeypatch.setattr(health_relay,'MAX_POLL_BYTES',2*1024*1024)
    api.monkeypatch.setattr(health_relay,'MAX_POLL_LINES',1)
    assert get(api).status_code==502
    assert all(s.closed for s in api.streams) and all(c.is_closed for c in api.clients)


def test_upstream_connect_error_is_generic_and_client_closes(relay_api):
    api=relay_api;api.upstream(error=httpx.ConnectError('PRIVATE URL AND TOKEN MUST NOT LEAK'))
    response=get(api)
    assert response.status_code==502 and 'PRIVATE' not in response.text
    assert all(c.is_closed for c in api.clients)


def test_stream_fragmented_utf8_lines_and_late_bad_json_cleanup(relay_api):
    api=relay_api
    wire=frame('open')+frame(message='合成中文')+frame('keepalive')
    api.upstream(chunks=[wire[:11],wire[11:37],wire[37:]])
    response=get(api,'?since=10m')
    assert response.status_code==200
    assert [json.loads(line)['event'] for line in response.text.splitlines()]==['open','message','keepalive']
    assert '合成中文' in response.text
    api.upstream(chunks=[frame('open'),b'PRIVATE INVALID JSON\n'])
    response=get(api,'?since=10m')
    assert response.status_code==200 and len(response.text.splitlines())==1
    assert 'PRIVATE' not in response.text
    assert all(s.closed for s in api.streams) and all(c.is_closed for c in api.clients)


def test_small_live_first_frame_is_not_buffered_and_disconnect_closes(relay_api):
    api=relay_api
    async def run():
        class Live(httpx.AsyncByteStream):
            closed=False
            async def __aiter__(self):
                yield frame('open')
                await asyncio.Event().wait()
            async def aclose(self):self.closed=True
        live=Live()
        async def handle(request):return httpx.Response(200,headers={'content-type':'application/x-ndjson'},stream=live)
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        api.monkeypatch.setattr(health_relay,'new_upstream_client',lambda:client)
        db=api.factory()
        response=await asyncio.wait_for(health_relay.relay(db,'0','10m'),1)
        assert db.released
        assert json.loads(await anext(response.body_iterator))['event']=='open'
        await response.body_iterator.aclose()
        assert live.closed and client.is_closed
    asyncio.run(run())


def test_cancel_during_first_upstream_frame_closes_response_and_client(relay_api):
    api=relay_api
    async def run():
        started=asyncio.Event()
        class Hanging(httpx.AsyncByteStream):
            closed=False
            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b''
            async def aclose(self):self.closed=True
        hanging=Hanging()
        async def handle(request):return httpx.Response(200,headers={'content-type':'application/x-ndjson'},stream=hanging)
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        api.monkeypatch.setattr(health_relay,'new_upstream_client',lambda:client)
        db=api.factory();task=asyncio.create_task(health_relay.relay(db,'0','10m'))
        await asyncio.wait_for(started.wait(),1);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert db.released and hanging.closed and client.is_closed
    asyncio.run(run())


def test_disconnect_sending_headers_before_body_iteration_closes_resources(relay_api):
    api=relay_api
    async def run():
        stream=Frames([frame('open')])
        async def handle(request):return httpx.Response(200,headers={'content-type':'application/x-ndjson'},stream=stream)
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        api.monkeypatch.setattr(health_relay,'new_upstream_client',lambda:client)
        response=await health_relay.relay(api.factory(),'0','10m')
        async def disconnected(message):raise OSError('Synthetic disconnected sender')
        async def receive():return {'type':'http.disconnect'}
        with pytest.raises(Exception):
            await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,disconnected)
        assert stream.closed and client.is_closed
    asyncio.run(run())


def test_anyio_cancel_during_httpx_automatic_eof_close_finishes_resource_release(relay_api):
    import anyio
    api=relay_api
    async def run():
        started=anyio.Event()
        class SlowClose(httpx.AsyncByteStream):
            released=False
            async def __aiter__(self):yield frame('open')
            async def aclose(self):
                started.set()
                await anyio.sleep(0.02)
                self.released=True
        upstream=SlowClose()
        async def handle(request):return httpx.Response(200,headers={'content-type':'application/x-ndjson'},stream=upstream)
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        api.monkeypatch.setattr(health_relay,'new_upstream_client',lambda:client)
        response=await health_relay.relay(api.factory(),'0','10m')
        with anyio.CancelScope() as cancelled:
            async with anyio.create_task_group() as tasks:
                async def consume():
                    async for _ in response.body_iterator:pass
                async def interrupt_eof_close():
                    await started.wait();cancelled.cancel()
                tasks.start_soon(consume);tasks.start_soon(interrupt_eof_close)
        assert upstream.released and client.is_closed
    anyio.run(run)
