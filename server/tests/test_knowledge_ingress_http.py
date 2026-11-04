"""Real plugin -> loopback HTTP -> API/SQLite/files/worker acceptance.

The external WeChat event and SDK cache-directory getters are explicit fixtures.
No external chat, QQ account, production identity or provider request is used.
"""
import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import httpx
import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
import uvicorn

from app.config import get_settings
from app.knowledge_models import KnowledgeAsset, KnowledgeCloudUsage, KnowledgeIngressReceipt, KnowledgeItem, KnowledgeJob
from app.models import Staff, StaffChannel, UserAccount
from app.database import Base, get_db
from app.auth import create_user_session
from app import knowledge, knowledge_asr, knowledge_worker
from test_knowledge import app, kb

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = Path(os.environ.get('KNOWLEDGE_TEST_PUBLIC_SILK') or
              Path(__file__).resolve().parent / 'fixtures' / 'public-tencent-fixture.amr')
DIGEST = 'e267dac1cef824b188e5c8659a513bbe147dc140fbefdf90ec2fc92ea0c734f3'


@pytest.fixture
def ingress(kb, monkeypatch):
    _, _, _, tmp = kb
    url = os.environ.get('KNOWLEDGE_TEST_DATABASE_URL') or 'sqlite:///' + (tmp / 'ingress.db').as_posix()
    parsed = make_url(url)
    if parsed.get_backend_name() != 'sqlite':
        assert parsed.get_backend_name() == 'mysql' and parsed.database and parsed.database.startswith('signaldesk_kb_test_'), 'Only a dedicated prefixed MySQL test schema is allowed'
    engine = create_engine(url, pool_pre_ping=True, connect_args={'check_same_thread':False, 'timeout':30} if parsed.get_backend_name()=='sqlite' else {})
    assert not inspect(engine).get_table_names(), 'Test schema must be empty; refusing to modify existing tables'
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    users=[]
    with factory() as db:
        for n in range(3):
            staff=Staff(name=f'附件合成成员{n}');staff.channels=[StaffChannel(platform='weixin',recipient_id=f'preview-sender-{n}')]
            user=UserAccount(username=f'ingress-check-{n}',password_hash='unused',staff=staff,role='admin' if n==2 else 'member')
            db.add(user);db.flush();token,_=create_user_session(db,user);users.append({'Authorization':'Bearer '+token})
        db.commit()
    prior_session_override=app.dependency_overrides[get_db]
    def sessions():
        with factory() as db:yield db
    app.dependency_overrides[get_db]=sessions
    plugin_path = Path(os.environ.get('KNOWLEDGE_TEST_PLUGIN_PATH') or ROOT / 'hermes-plugin/wechat-knowledge/__init__.py')
    spec = importlib.util.spec_from_file_location('kb_ingress_acceptance_plugin_' + secrets.token_hex(4), plugin_path)
    plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
    cache = tmp / 'adapter-cache'; cache.mkdir()
    sdk = ModuleType('gateway.platforms.base')
    for kind in ['image', 'audio', 'video', 'document']:
        folder = cache / kind; folder.mkdir()
        setattr(sdk, 'get_' + kind + '_cache_dir', lambda folder=folder: folder)
    monkeypatch.setitem(sys.modules, 'gateway.platforms.base', sdk)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False, lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline: time.sleep(.01)
    assert server.started, 'Local API did not become ready'
    base = f'http://127.0.0.1:{port}'; secret = get_settings().knowledge_gateway_secret
    monkeypatch.setattr(plugin, 'connection', lambda: (base, secret))
    opener = build_opener(ProxyHandler({})); calls = []
    def open_request(request, timeout):
        assert request.full_url.startswith(base + '/api/v1/knowledge/')
        call = {'url': request.full_url.removeprefix(base), 'status': None}; calls.append(call)
        try:
            response = opener.open(request, timeout=timeout)
            call['status'] = response.status
            assert response.headers.get_content_type() == 'application/json'
            return response
        except HTTPError as error:
            call['status'] = error.code
            assert error.headers.get_content_type() == 'application/json'
            raise
    monkeypatch.setattr(plugin, 'urlopen', open_request)
    registered = {}
    plugin.register(SimpleNamespace(register_hook=lambda name, fn: registered.update(hook=fn), register_command=lambda name, fn, **kw: registered.update(command=fn)))
    def event(files=None, text='收藏附件到我的知识库', sender='preview-sender-0', chat='dm', message='public-inbound-001'):
        return SimpleNamespace(text=text, source=SimpleNamespace(platform='weixin', user_id=sender, chat_type=chat), media_urls=files or [], media_types=['audio'] if files else [], message_id=message)
    def deliver(e):
        rewrite = registered['hook'](event=e, gateway=None)
        if not rewrite: return None
        assert rewrite['action'] == 'rewrite'
        return asyncio.run(registered['command'](rewrite['text'].split(' ', 1)[1]))
    def request(method, path, owner=0, **kwargs):
        response = httpx.request(method, base + '/api/v1/knowledge' + path, headers=users[owner], trust_env=False, timeout=20, **kwargs)
        assert response.headers['content-type'].startswith('application/json')
        return response
    def source(name='doc_012345abcdef_public-voice.amr', data=None):
        target = cache / 'audio' / name; target.write_bytes(PUBLIC.read_bytes() if data is None else data); return target
    state = SimpleNamespace(plugin=plugin, calls=calls, cache=cache, event=event, deliver=deliver, request=request, source=source, factory=factory, tmp=tmp, users=users, base=base, secret=secret)
    try: yield state
    finally:
        server.should_exit = True; thread.join(timeout=10); sock.close(); assert not thread.is_alive()
        app.dependency_overrides[get_db]=prior_session_override
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables), 'Unexpected tables; refusing destructive cleanup'
        Base.metadata.drop_all(engine);engine.dispose()


def attachment_headers(s, body, *, sender='preview-sender-0', message='direct-signature-001', filename='voice.amr', nonce=None, stamp=None, digest=None, extra=None):
    meta = dict(platform='weixin', sender_id=sender, chat_type='dm', message_id=message, filename=filename)
    if extra: meta.update(extra)
    encoded = base64.b64encode(json.dumps(meta, separators=(',', ':')).encode()).decode()
    timestamp = str(int(time.time()) if stamp is None else stamp); nonce = nonce or secrets.token_urlsafe(24)
    digest = digest or hashlib.sha256(body).hexdigest()
    signature = hmac.new(s.secret.encode(), f'{timestamp}\n{nonce}\n{encoded}\n{digest}'.encode(), hashlib.sha256).hexdigest()
    return {'Content-Type':'application/octet-stream', 'X-Knowledge-Time':timestamp, 'X-Knowledge-Nonce':nonce,
            'X-Knowledge-Signature':signature, 'X-Knowledge-Metadata':encoded, 'X-Knowledge-Content-SHA256':digest}


def signed_upload(s, body, **kwargs):
    headers = attachment_headers(s, body, **kwargs)
    r = httpx.post(s.base + '/api/v1/knowledge/gateway/attachment', headers=headers, content=body, timeout=20, trust_env=False)
    assert r.headers['content-type'].startswith('application/json'); return r


def test_registered_hook_real_http_preserves_public_tencent_original_and_no_paid_model(ingress):
    s = ingress; source = s.source(); body = source.read_bytes(); assert hashlib.sha256(body).hexdigest() == DIGEST
    e = s.event([str(source)]); answer = s.deliver(e)
    assert '原件已保存' in answer and '没有自动发起付费识别' in answer
    assert e.media_urls == e.media_types == []
    assert s.calls == [{'url':'/api/v1/knowledge/gateway/attachment','status':200}]
    listed = s.request('GET', '/items').json(); assert listed['total'] == 1
    itemid = listed['items'][0]['id']; item = s.request('GET', '/items/' + itemid).json()
    assert item['assets'][0]['filename'] == 'public-voice.amr'
    with s.factory() as db:
        asset = db.get(KnowledgeAsset, item['assets'][0]['id'])
        assert asset.sha256 == DIGEST and asset.size == len(body)
        assert (Path(get_settings().knowledge_storage_dir) / asset.storage_name).read_bytes() == body
        assert db.scalar(select(KnowledgeCloudUsage.id)) is None
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.item_id == itemid)); assert job.mode not in {'qwen_asr','volc_asr','doubao_ocr'}
    assert knowledge_worker.run_once(s.factory)
    assert s.request('GET', '/items/' + itemid).json()['status'] == 'needs_input'
    assert s.request('GET', '/usage').json()['daily_submissions'] == 0
    assert s.request('GET', '/items/' + itemid, owner=1).status_code == 404
    assert s.request('GET', '/items/' + itemid, owner=2).status_code == 404
    assert source.read_bytes() == body


def test_explicit_weixin_attachment_ignores_text_identity_and_persists_owner_scope(ingress):
    s=ingress; source=s.source()
    assert s.deliver(s.event([str(source)], sender='unbound-synthetic'))
    assert s.calls[-1]['status'] == 403 and s.request('GET','/items').json()['total'] == 0
    assert s.deliver(s.event([str(source)], sender='preview-sender-1'))
    assert s.request('GET','/items',owner=1).json()['total'] == 1
    assert s.request('GET','/items').json()['total'] == 0
    assert s.deliver(s.event([str(source)],text='收藏附件到我的知识库 owner_id=1')) is None


def test_group_and_unrequested_media_are_not_uploaded_or_sent_to_stt(ingress):
    s=ingress; source=s.source()
    ordinary=s.event([str(source)],text='ordinary message'); assert s.deliver(ordinary) is None
    assert ordinary.media_urls == [str(source)]
    assert '私聊' in s.deliver(s.event([str(source)],chat='group'))
    assert not s.calls and s.request('GET','/items').json()['total'] == 0
    assert s.deliver(SimpleNamespace(text='收藏附件到我的知识库',media_urls=[str(source)])) is None


def test_attachment_mode_is_private_owner_scoped_and_expires(ingress):
    s=ingress; source=s.source()
    assert '已开启' in s.deliver(s.event(text='我的知识库附件收藏：开启'))
    assert s.deliver(s.event([str(source)],text='voice',sender='preview-sender-1')) is None
    assert s.deliver(s.event([str(source)],text='voice',chat='group')) is None
    assert '原件已保存' in s.deliver(s.event([str(source)],text='voice'))
    assert '已关闭' in s.deliver(s.event(text='我的知识库附件收藏：关闭'))
    assert s.deliver(s.event([str(source)],text='voice')) is None
    assert '已开启' in s.deliver(s.event(text='我的知识库附件收藏：开启'))
    s.plugin._ATTACHMENT_MODES[('weixin','preview-sender-0')] = 0
    assert s.deliver(s.event([str(source)],text='voice')) is None


@pytest.mark.parametrize('autoflush',[False,True])
def test_signature_time_nonce_metadata_and_sender_are_fail_closed(ingress,autoflush):
    s=ingress; body=PUBLIC.read_bytes()
    s.factory.configure(autoflush=autoflush)
    assert signed_upload(s,body,stamp=int(time.time())-120).status_code == 401
    assert signed_upload(s,body,nonce='short').status_code == 401
    assert signed_upload(s,body,extra={'owner_id':1}).status_code == 401
    assert signed_upload(s,body,sender='not-bound').status_code == 403
    headers=attachment_headers(s,body);headers['X-Knowledge-Signature']='0'*64
    assert httpx.post(s.base+'/api/v1/knowledge/gateway/attachment',content=body,headers=headers,trust_env=False).status_code==401
    good=attachment_headers(s,body)
    first=httpx.post(s.base+'/api/v1/knowledge/gateway/attachment',content=body,headers=good,trust_env=False)
    assert first.status_code==200
    assert httpx.post(s.base+'/api/v1/knowledge/gateway/attachment',content=body,headers=good,trust_env=False).status_code==409


def test_same_message_is_deduplicated_and_deleted_original_is_not_resurrected(ingress):
    s=ingress; source=s.source();e=lambda:s.event([str(source)])
    assert '原件已保存' in s.deliver(e())
    assert '不重复入库' in s.deliver(e())
    listed=s.request('GET','/items').json();assert listed['total']==1;item=listed['items'][0]
    removed=s.request('DELETE',f"/items/{item['id']}?revision={item['revision']}");assert removed.status_code==200
    assert '不重复入库' in s.deliver(e())
    assert s.request('GET','/items').json()['total']==0
    assert s.request('GET','/items?state=removed').json()['total']==1


def test_wrong_body_is_never_published_and_unknown_receipt_does_not_replay(ingress):
    s=ingress; body=PUBLIC.read_bytes()
    first=signed_upload(s,body,digest='0'*64)
    assert first.status_code==422
    assert signed_upload(s,body,digest='0'*64).status_code==409
    with s.factory() as db:
        assert db.scalar(select(KnowledgeAsset.id)) is None
        assert db.scalar(select(KnowledgeIngressReceipt.state))=='unknown'
    assert not list(Path(get_settings().knowledge_storage_dir).glob('*.part'))


def test_max_files_unsafe_paths_and_oversize_cache_are_refused_before_http(ingress):
    s=ingress;source=s.source()
    assert '最多 5' in s.deliver(s.event([str(source)]*6))
    outside=s.tmp/'outside.amr';outside.write_bytes(PUBLIC.read_bytes())
    for path in [str(outside), 'relative.amr', str(s.cache/'audio'), 'https://untrusted.invalid/audio.amr']:
        assert '未保存' in s.deliver(s.event([path]))
    huge=s.cache/'audio/huge.amr'
    with huge.open('wb') as f:f.truncate(500*1024*1024+1)
    assert '未保存' in s.deliver(s.event([str(huge)]))
    assert not s.calls


def test_multiple_attachment_bodies_and_filenames_are_not_mixed(ingress):
    s=ingress;voice=s.source();text=s.cache/'document/doc_abcdef012345_note.txt';text.write_bytes(b'synthetic document body')
    answer=s.deliver(s.event([str(voice),str(text)],message='multiple-public-001'))
    assert answer.count('原件已保存')==2 and len(s.calls)==2
    listed=s.request('GET','/items').json();assert listed['total']==2
    with s.factory() as db:
        assets=list(db.scalars(select(KnowledgeAsset)));assert len(assets)==2
        assert {a.filename:a.sha256 for a in assets}=={'public-voice.amr':DIGEST,'note.txt':hashlib.sha256(text.read_bytes()).hexdigest()}
        assert db.scalar(select(KnowledgeCloudUsage.id)) is None


def test_concurrent_identical_nonce_accepts_exactly_once_without_500(ingress):
    from concurrent.futures import ThreadPoolExecutor
    s=ingress;body=PUBLIC.read_bytes();headers=attachment_headers(s,body,message='concurrent-signed-001')
    def submit(_):
        response=httpx.post(s.base+'/api/v1/knowledge/gateway/attachment',content=body,headers=headers,trust_env=False,timeout=30)
        assert response.headers['content-type'].startswith('application/json')
        return response.status_code
    with ThreadPoolExecutor(max_workers=4) as pool:statuses=list(pool.map(submit,range(4)))
    assert sorted(statuses)==[200,409,409,409]
    with s.factory() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeAsset))==1
        assert db.scalar(select(func.count()).select_from(KnowledgeIngressReceipt))==1


def test_api_upload_limit_and_unsupported_format_do_not_publish_assets(ingress,monkeypatch):
    s=ingress;monkeypatch.setattr(get_settings(),'knowledge_max_upload_mb',1)
    assert signed_upload(s,b'x'*(1024*1024+1),filename='voice.amr').status_code==413
    assert signed_upload(s,b'data',filename='script.exe',message='unsupported-1').status_code==422
    with s.factory() as db:assert db.scalar(select(KnowledgeAsset.id)) is None


def test_hardlink_cannot_read_outside_adapter_cache(ingress):
    s=ingress;outside=s.tmp/'outside-synthetic-secret.amr';outside.write_bytes(b'synthetic outside cache content')
    link=s.cache/'audio/linked.amr';os.link(outside,link)
    answer=s.deliver(s.event([str(link)]))
    assert '未保存' in answer
    assert not s.calls


def test_file_changed_while_hashing_is_refused_before_http(ingress,monkeypatch):
    s=ingress;source=s.source();before=source.stat();real_sha=hashlib.sha256;created=[]
    class MutatingDigest:
        def __init__(self):self.inner=real_sha();self.changed=False
        def update(self,data):
            self.inner.update(data)
            if not self.changed:
                self.changed=True;source.write_bytes(b'z'*before.st_size);os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000_000))
        def __getattr__(self,name):return getattr(self.inner,name)
    def digest(*args,**kwargs):
        if not created:created.append(True);return MutatingDigest()
        return real_sha(*args,**kwargs)
    monkeypatch.setattr(s.plugin,'hashlib',SimpleNamespace(sha256=digest))
    answer=s.deliver(s.event([str(source)]))
    assert '未保存' in answer
    assert not s.calls


def test_cache_changed_during_socket_upload_cannot_change_verified_snapshot(ingress,monkeypatch):
    import tempfile
    s=ingress;source=s.source();original=source.read_bytes();open_request=s.plugin.urlopen;transmitted=[];snapshots=[];changed=[]
    real_temporary=tempfile.TemporaryFile;real_fdopen=os.fdopen
    class ReadRace:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def __getattr__(self,name):return getattr(self.stream,name)
        def read(self,size=-1):
            # Change the cache *inside the first socket-body read*, after any
            # preceding metadata check. A check/read guard alone cannot cover it.
            if size==256*1024 and not changed:
                source.write_bytes(b'z'*len(original));changed.append(True)
            return self.stream.read(size)
    class OSProxy:
        def __getattr__(self,name):return getattr(os,name)
        def fdopen(self,*args,**kwargs):return ReadRace(real_fdopen(*args,**kwargs))
    monkeypatch.setattr(s.plugin,'os',OSProxy())
    def temporary():
        stream=real_temporary();snapshots.append(stream);return ReadRace(stream)
    monkeypatch.setattr(s.plugin,'tempfile',SimpleNamespace(TemporaryFile=temporary),raising=False)
    def observe_send(request,timeout):
        chunks=request.data
        def tee():
            for block in chunks:transmitted.append(block);yield block
        length=request.get_header('Content-length')
        request.data=tee()
        if length:request.add_header('Content-Length',length)
        return open_request(request,timeout)
    monkeypatch.setattr(s.plugin,'urlopen',observe_send)
    answer=s.deliver(s.event([str(source)],message='changed-after-snapshot'))
    assert b''.join(transmitted)==original, 'Socket read carried mutated cache bytes'
    assert '原件已保存' in answer
    assert changed and source.exists() and source.read_bytes()==b'z'*len(original)
    assert snapshots and all(stream.closed for stream in snapshots)
    with s.factory() as db:
        asset=db.scalar(select(KnowledgeAsset));assert asset.sha256==DIGEST
        assert (Path(get_settings().knowledge_storage_dir)/asset.storage_name).read_bytes()==original


def test_attachment_to_worker_mock_qwen_draft_review_and_time_citation(ingress,monkeypatch):
    s=ingress;source=s.source();assert '原件已保存' in s.deliver(s.event([str(source)]))
    assert knowledge_worker.run_once(s.factory)
    item=s.request('GET','/items').json()['items'][0];detail=s.request('GET','/items/'+item['id']).json()
    settings=get_settings()
    for key,value in {'knowledge_cloud_asr_provider':'qwen','knowledge_cloud_asr_enabled':True,'knowledge_cloud_asr_model':'qwen3-asr-flash','knowledge_qwen_api_key':'synthetic-only-key','knowledge_qwen_base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1','knowledge_silk_enabled':True}.items():monkeypatch.setattr(settings,key,value)
    monkeypatch.setattr(knowledge_asr,'silk_configuration',lambda:True)
    monkeypatch.setattr(knowledge,'silk_configuration',lambda:True)
    monkeypatch.setattr(knowledge_asr.shutil,'which',lambda _: 'synthetic-local-converter')
    import wave
    def prepare(path,filename,output):
        assert hashlib.sha256(path.read_bytes()).hexdigest()==DIGEST
        # Explicit conversion fixture. Actual Linux native decode was separately
        # accepted in 0.6.2; no native codec is pretended on this Windows host.
        with wave.open(str(output),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*34240)
        return 2.14
    calls=[]
    def provider(request):
        calls.append(request);assert str(request.url).endswith('/chat/completions')
        body=json.loads(request.content);assert body['model']=='qwen3-asr-flash'
        assert body['messages'][0]['content'][0]['input_audio']['data'].startswith('data:audio/wav;base64,')
        return httpx.Response(200,json={'id':'mock-ingress-asr','choices':[{'finish_reason':'stop','message':{'content':'软件合成附件转写验收：原件先保存，核对后才能引用。'}}]})
    original=knowledge_asr.recognize
    monkeypatch.setattr(knowledge_asr,'recognize',lambda *args,**kw:original(*args,**kw,transport=httpx.MockTransport(provider),audio_preparer=prepare))
    endpoint=f"/items/{detail['id']}/assets/{detail['assets'][0]['id']}/asr"
    assert s.request('POST',endpoint,json={'revision':detail['revision'],'cloud_consent':False}).status_code==422
    response=s.request('POST',endpoint,json={'revision':detail['revision'],'cloud_consent':True});assert response.status_code==200,response.text
    assert knowledge_worker.run_once(s.factory)
    draft=s.request('GET','/items/'+detail['id']).json();assert draft['status']=='needs_review' and draft['method']=='qwen_asr_draft'
    assert not s.request('POST','/ask',json={'question':'软件合成附件转写验收'}).json()['citations']
    approved=s.request('POST',f"/items/{draft['id']}/asr/confirm",json={'revision':draft['revision'],'confirmation':'已核对'})
    assert approved.status_code==200 and approved.json()['method']=='qwen_asr_reviewed'
    answer=s.request('POST','/ask',json={'question':'软件合成附件转写验收'}).json()
    assert answer['citations'][0]['item_id']==detail['id'] and '2.14s' in answer['citations'][0]['locator']
    assert len(calls)==1 and s.request('GET','/usage').json()['daily_submissions']==1
    assert source.read_bytes()==PUBLIC.read_bytes()
