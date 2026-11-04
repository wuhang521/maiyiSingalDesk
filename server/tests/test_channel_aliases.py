"""Actual SQLite/API acceptance with synthetic channels, articles and operators."""
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import quote
import os
import secrets

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

# Match the existing API module's synthetic key before config is first cached,
# so this module is runnable alone or first in a combined collection.
os.environ.setdefault('TASK_API_KEY', 'test-key')

from app import articles, auth
from app.database import Base, get_db
from app.models import Article, ArticleUserState, SmartChannel, utcnow


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'alias-api.sqlite'),
                           connect_args={'check_same_thread': False})
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    # Also lets this acceptance run against the pre-fix app for red evidence.
    with engine.begin() as c:
        c.execute(text('CREATE TABLE IF NOT EXISTS smart_channel_alias '
                       '(alias_id VARCHAR(80) PRIMARY KEY, canonical_id VARCHAR(80) NOT NULL)'))
    key = secrets.token_urlsafe(24)
    monkeypatch.setattr(auth, 'get_settings', lambda: SimpleNamespace(api_key=key))
    app = FastAPI()
    app.include_router(articles.router)

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    with factory() as db:
        db.add(SmartChannel(id='health', name='Synthetic managed intelligence', description='current rules',
                            prompt='current prompt', topic='synthetic-private-topic', server='https://push.example.test',
                            schedule='09:00', max_items=100, include_terms=['business'], exclude_terms=['diet'],
                            color='green', state='active'))
        db.add(SmartChannel(id='ordinary', name='Ordinary', topic='ordinary-topic', state='paused'))
        db.execute(text('INSERT INTO smart_channel_alias(alias_id, canonical_id) '
                        'VALUES (:alias,:canonical)'), {'alias':'demo-health','canonical':'health'})
        db.commit()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, factory=factory, headers={'X-SignalDesk-Key':key})
    engine.dispose()


def payload(channel='demo-health', **values):
    return {'id':channel, 'name':'Obsolete cache name', 'description':'old', 'prompt':'old',
            'topic':'', 'server':'', 'schedule':'', 'maxItems':20,
            'includeTerms':[], 'excludeTerms':[], 'color':'red', 'state':'draft', **values}


def item(article_id='daily:health:synthetic:1', channel='demo-health', **values):
    return {'id':article_id, 'channelId':channel, 'title':'Synthetic business article',
            'body':'Synthetic original body', 'source':'fixture.example.test',
            'clickUrl':'https://fixture.example.test/article', 'timestamp':1786400000123,
            'tags':['business'], 'priority':4, **values}


def snapshot(model):
    return {column.name:getattr(model,column.name) for column in model.__table__.columns}


def channel_snapshot(api):
    with api.factory() as db:
        return snapshot(db.get(SmartChannel,'health'))


def create_article(api, article_id='preserved-id', channel='health'):
    with api.factory() as db:
        article = Article(id=article_id, channel_id=channel, title='Original synthetic title',
                          body='Original synthetic body', source='fixture.example.test',
                          click_url='https://fixture.example.test/original', tags=['original'], priority=5,
                          published_at=utcnow()-timedelta(days=3), content_hash='a'*64)
        db.add(article)
        db.flush()
        now = utcnow()
        db.add(ArticleUserState(operator_name='synthetic-a', article_id=article_id,
                                read=True, saved=True, feedback='useful', deleted_at=now,
                                purge_at=now+timedelta(days=7), state_updated_at_ms=900))
        db.add(ArticleUserState(operator_name='synthetic-b', article_id=article_id,
                                read=False, saved=True, feedback='not-useful', deleted_at=now,
                                purge_at=now+timedelta(days=7), permanently_deleted_at=now,
                                state_updated_at_ms=1200))
        db.commit()
        return snapshot(article), {name:snapshot(db.get(ArticleUserState,(name,article_id)))
                                   for name in ('synthetic-a','synthetic-b')}


def personal(api, name='synthetic-a'):
    return {**api.headers, 'X-SignalDesk-Operator':quote(name)}


def test_list_canonical_contract_hides_remaining_alias_entity(api):
    with api.factory() as db:
        db.add(SmartChannel(id='demo-health',name='Stale duplicate',topic='stale'))
        db.commit()
    response=api.client.get('/api/v1/channels',headers=api.headers)
    assert response.status_code==200
    rows={r['id']:r for r in response.json()}
    assert set(rows)=={'health','ordinary'}
    assert rows['health']['aliases']==['demo-health'] and rows['health']['managed'] is True
    assert rows['ordinary']['aliases']==[] and rows['ordinary']['managed'] is False


@pytest.mark.parametrize('channel', ['health','demo-health'])
@pytest.mark.parametrize('query', ['', '?preserve_connection=true', '?preserve_connection=false'])
def test_obsolete_channel_posts_are_complete_idempotent_noops(api,channel,query):
    before=channel_snapshot(api)
    for _ in range(2):
        response=api.client.post('/api/v1/channels'+query,headers=api.headers,json=payload(channel))
        assert response.status_code==200 and response.json()=={'id':'health'}
    assert channel_snapshot(api)==before
    with api.factory() as db:
        assert db.get(SmartChannel,'demo-health') is None


def test_alias_delete_is_idempotent_and_canonical_delete_is_conflict(api):
    before=channel_snapshot(api)
    for _ in range(2):
        response=api.client.delete('/api/v1/channels/demo-health',headers=api.headers)
        assert response.status_code==204 and not response.content
    response=api.client.delete('/api/v1/channels/health',headers=api.headers)
    assert response.status_code==409 and isinstance(response.json()['detail'],str)
    assert channel_snapshot(api)==before
    rows=api.client.get('/api/v1/channels',headers=api.headers).json()
    assert next(r for r in rows if r['id']=='health')['aliases']==['demo-health']


def test_mapping_absence_preserves_unmanaged_health_crud(api):
    with api.factory() as db:
        db.execute(text('DELETE FROM smart_channel_alias'))
        db.commit()
    response=api.client.post('/api/v1/channels',headers=api.headers,json=payload('health',topic='new-topic'))
    assert response.status_code==200
    rows=api.client.get('/api/v1/channels',headers=api.headers).json()
    health=next(r for r in rows if r['id']=='health')
    assert health['topic']=='new-topic' and health['managed'] is False
    assert api.client.delete('/api/v1/channels/health',headers=api.headers).status_code==204


def test_unmanaged_channel_preserve_connection_behavior_stays_compatible(api):
    response=api.client.post('/api/v1/channels?preserve_connection=true',headers=api.headers,
                             json=payload('ordinary',name='Edited ordinary'))
    assert response.status_code==200
    with api.factory() as db:
        row=db.get(SmartChannel,'ordinary')
        assert row.name=='Edited ordinary' and row.topic=='ordinary-topic' and row.state=='paused'
    assert api.client.delete('/api/v1/channels/ordinary',headers=api.headers).status_code==204
    assert api.client.delete('/api/v1/channels/missing',headers=api.headers).status_code==404


@pytest.mark.parametrize('route', ['batch','sync'])
def test_new_article_alias_normalization_and_replay_no_duplicate(api,route):
    incoming=item()
    if route=='sync':incoming.update(read=True,saved=True,stateUpdatedAt=100)
    for _ in range(2):
        response=api.client.post('/api/v1/articles/'+route,headers=personal(api),json={'items':[incoming]})
        assert response.status_code==200
    with api.factory() as db:
        rows=db.scalars(select(Article)).all()
        assert len(rows)==1 and rows[0].channel_id=='health' and rows[0].id==incoming['id']
    result=api.client.get('/api/v1/articles?state=all',headers=personal(api)).json()['items']
    assert len(result)==1 and result[0]['channelId']=='health'


@pytest.mark.parametrize('route', ['batch','sync'])
@pytest.mark.parametrize('incoming_channel',['demo-health','ordinary'])
def test_managed_article_cache_never_changes_original_or_other_person_state(api,route,incoming_channel):
    before,states=create_article(api)
    incoming=item('preserved-id',incoming_channel,title='Obsolete title',body='Obsolete cache body',
                  timestamp=1,read=False,saved=False,feedback=None,deletedAt=None,purged=False,stateUpdatedAt=100)
    response=api.client.post('/api/v1/articles/'+route,headers=personal(api),json={'items':[incoming]})
    assert response.status_code==200
    with api.factory() as db:
        assert snapshot(db.get(Article,'preserved-id'))==before
        assert {name:snapshot(db.get(ArticleUserState,(name,'preserved-id'))) for name in states}==states
    assert api.client.get('/api/v1/articles?state=active',headers=personal(api)).json()['total']==0
    assert api.client.get('/api/v1/articles?state=trash',headers=personal(api)).json()['total']==1
    assert api.client.get('/api/v1/articles?state=all',headers=personal(api,'synthetic-b')).json()['total']==0


def test_newer_personal_state_still_updates_only_current_operator(api):
    before,states=create_article(api)
    incoming=item('preserved-id',read=False,saved=False,feedback='not-useful',
                  deletedAt=int(states['synthetic-a']['deleted_at'].timestamp()*1000),stateUpdatedAt=901)
    response=api.client.post('/api/v1/articles/sync',headers=personal(api),json={'items':[incoming]})
    assert response.status_code==200 and response.json()['accepted']==1
    with api.factory() as db:
        assert snapshot(db.get(Article,'preserved-id'))==before
        state=db.get(ArticleUserState,('synthetic-a','preserved-id'))
        assert (state.read,state.saved,state.feedback,state.state_updated_at_ms)==(False,False,'not-useful',901)
        assert snapshot(db.get(ArticleUserState,('synthetic-b','preserved-id')))==states['synthetic-b']


@pytest.mark.parametrize('order',[('daily:health:fixture:1','ntfy-fixture:1'),('ntfy-fixture:1','daily:health:fixture:1')])
def test_same_content_dedup_both_orders_preserves_id_publication_and_state(api,order):
    first=item(order[0],timestamp=1786400000123)
    second=item(order[1],channel='health',timestamp=1786409999999)
    assert api.client.post('/api/v1/articles/batch',headers=api.headers,json={'items':[first]}).status_code==200
    changed=api.client.patch('/api/v1/articles/'+order[0]+'/state',headers=personal(api),
                             json={'read':True,'saved':True,'stateUpdatedAt':100})
    assert changed.status_code==200
    assert api.client.post('/api/v1/articles/batch',headers=api.headers,json={'items':[second]}).status_code==200
    with api.factory() as db:
        rows=db.scalars(select(Article)).all()
        assert len(rows)==1 and rows[0].id==order[0] and rows[0].channel_id=='health'
        assert articles.milliseconds(rows[0].published_at)==first['timestamp']
        state=db.get(ArticleUserState,('synthetic-a',order[0]))
        assert state.read and state.saved and state.state_updated_at_ms==100


def test_both_historical_ids_and_all_states_survive_cache_posts(api):
    first,state_a=create_article(api,'history-canonical-id')
    second,state_b=create_article(api,'history-alias-id')
    with api.factory() as db:
        # Simulate a completed migration: same historic IDs, both now canonical.
        assert len(db.scalars(select(Article)).all())==2
    response=api.client.post('/api/v1/articles/sync',headers=personal(api),json={'items':[
        item('history-canonical-id',stateUpdatedAt=0),item('history-alias-id',stateUpdatedAt=0)]})
    assert response.status_code==200 and response.json()['accepted']==0
    with api.factory() as db:
        assert snapshot(db.get(Article,'history-canonical-id'))==first
        assert snapshot(db.get(Article,'history-alias-id'))==second
        for article_id,expected in [('history-canonical-id',state_a),('history-alias-id',state_b)]:
            assert {n:snapshot(db.get(ArticleUserState,(n,article_id))) for n in expected}==expected


def test_managed_personal_explicit_state_actions_remain_available(api):
    create_article(api)
    headers=personal(api)
    restored=api.client.patch('/api/v1/articles/preserved-id/state',headers=headers,
                              json={'action':'restore','stateUpdatedAt':901})
    assert restored.status_code==200 and restored.json()['deletedAt'] is None
    assert api.client.get('/api/v1/articles?state=active',headers=headers).json()['total']==1
    deleted=api.client.patch('/api/v1/articles/preserved-id/state',headers=headers,
                             json={'action':'delete','stateUpdatedAt':902})
    assert deleted.status_code==200 and deleted.json()['deletedAt'] is not None
    purged=api.client.patch('/api/v1/articles/preserved-id/state',headers=headers,
                            json={'action':'permanent','stateUpdatedAt':903})
    assert purged.status_code==200 and purged.json()['purged'] is True


def test_bad_alias_target_fails_closed_without_creating_duplicate(api):
    with api.factory() as db:
        db.execute(text('UPDATE smart_channel_alias SET canonical_id=:target'),{'target':'missing-target'})
        db.commit()
    assert api.client.post('/api/v1/channels',headers=api.headers,json=payload()).status_code==409
    assert api.client.post('/api/v1/articles/batch',headers=api.headers,json={'items':[item()]}).status_code==409
    with api.factory() as db:
        assert db.get(SmartChannel,'demo-health') is None
        assert not db.scalars(select(Article)).all()


def test_cycle_alias_fails_closed(api):
    with api.factory() as db:
        db.execute(text('INSERT INTO smart_channel_alias(alias_id,canonical_id) VALUES (:a,:c)'),
                   {'a':'health','c':'demo-health'})
        db.commit()
    response=api.client.post('/api/v1/channels',headers=api.headers,json=payload())
    assert response.status_code==409


def test_bulk_error_rolls_back_preceding_flushed_article(api,monkeypatch):
    original=articles.upsert_article

    def inject_failure(db,incoming,*args,**kwargs):
        if incoming.id=='injected-failure':raise HTTPException(409,'Synthetic failure')
        return original(db,incoming,*args,**kwargs)

    monkeypatch.setattr(articles,'upsert_article',inject_failure)
    response=api.client.post('/api/v1/articles/batch',headers=api.headers,
                             json={'items':[item('must-rollback'),item('injected-failure')]})
    assert response.status_code==409
    with api.factory() as db:
        assert not db.scalars(select(Article)).all()


def test_alias_endpoints_keep_auth_operator_and_validation_errors(api):
    assert api.client.get('/api/v1/channels').status_code==401
    assert api.client.post('/api/v1/channels',headers=api.headers,json={'id':'demo-health'}).status_code==422
    assert api.client.post('/api/v1/articles/sync',headers=api.headers,json={'items':[item()]}).status_code==400
    assert api.client.post('/api/v1/articles/batch',headers=api.headers,
                            json={'items':[item(timestamp=-1)]}).status_code==422


def test_helper_contract_and_canonical_lock(api):
    from app.channel_aliases import aliases_for,is_managed,lock_managed_channels,resolve_channel_id
    with api.factory() as db:
        assert resolve_channel_id(db,'demo-health')=='health'
        assert resolve_channel_id(db,'ordinary')=='ordinary'
        assert aliases_for(db,'health')==['demo-health']
        assert aliases_for(db,'demo-health')==['demo-health']
        assert is_managed(db,'health') and is_managed(db,'demo-health')
        assert not is_managed(db,'ordinary')
        locked=lock_managed_channels(db,['demo-health','ordinary','health'])
        assert list(locked)==['health'] and locked['health'].topic=='synthetic-private-topic'


@pytest.mark.parametrize('route',['batch','sync'])
def test_reserved_health_status_is_never_an_article_or_personal_state(api,route):
    controls=[item('status-'+channel,channel,title='Synthetic delayed status',body='No article heading',
                   tags=['health_status'],read=True,saved=True,stateUpdatedAt=9000)
              for channel in ('health','demo-health')]
    for _ in range(2):
        response=api.client.post('/api/v1/articles/'+route,headers=personal(api),json={'items':controls})
        assert response.status_code==200 and response.json()=={'accepted':0}
    with api.factory() as db:
        assert not db.scalars(select(Article)).all()
        assert not db.scalars(select(ArticleUserState)).all()


def test_reserved_status_sync_cannot_change_existing_managed_original_or_state(api):
    before,states=create_article(api)
    control=item('preserved-id',tags=['health_status'],read=False,saved=False,stateUpdatedAt=99999)
    response=api.client.post('/api/v1/articles/sync',headers=personal(api),json={'items':[control]})
    assert response.status_code==200 and response.json()=={'accepted':0}
    with api.factory() as db:
        assert snapshot(db.get(Article,'preserved-id'))==before
        assert {n:snapshot(db.get(ArticleUserState,(n,'preserved-id'))) for n in states}==states


def test_same_status_tag_in_ordinary_channel_keeps_existing_semantics(api):
    incoming=item('ordinary-status','ordinary',tags=['health_status'],read=True,saved=True,stateUpdatedAt=100)
    for route in ('batch','sync'):
        response=api.client.post('/api/v1/articles/'+route,headers=personal(api),json={'items':[incoming]})
        assert response.status_code==200 and response.json()=={'accepted':1}
    with api.factory() as db:
        assert db.get(Article,'ordinary-status').channel_id=='ordinary'
        state=db.get(ArticleUserState,('synthetic-a','ordinary-status'))
        assert state.read and state.saved


def test_optin_id_map_resolves_preingested_original_for_personal_patch(api):
    original=item('daily:health:preingested:1','health')
    response=api.client.post('/api/v1/articles/batch',headers=api.headers,json={'items':[original]})
    assert response.status_code==200 and response.json()=={'accepted':1}
    with api.factory() as db:before=snapshot(db.get(Article,original['id']))
    incoming={**original,'id':'ntfy-ack:1','channelId':'demo-health','timestamp':original['timestamp']+999}
    response=api.client.post('/api/v1/articles/batch?resolve_ids=true',headers=api.headers,json={'items':[incoming]})
    assert response.status_code==200 and response.json()=={'accepted':1,'idMap':{incoming['id']:original['id']}}
    mapped=response.json()['idMap'][incoming['id']]
    changed=api.client.patch('/api/v1/articles/'+mapped+'/state',headers=personal(api),
                             json={'read':True,'saved':True,'stateUpdatedAt':1234})
    assert changed.status_code==200 and changed.json()['id']==original['id']
    assert changed.json()['read'] and changed.json()['saved']
    with api.factory() as db:
        assert snapshot(db.get(Article,original['id']))==before
        assert len(db.scalars(select(Article)).all())==1 and db.get(Article,incoming['id']) is None
        state=db.get(ArticleUserState,('synthetic-a',original['id']))
        assert state.read and state.saved


@pytest.mark.parametrize('query',['','?resolve_ids=false'])
def test_default_batch_contract_remains_accepted_only(api,query):
    response=api.client.post('/api/v1/articles/batch'+query,headers=api.headers,
                             json={'items':[item('ordinary-new','ordinary')]})
    assert response.status_code==200 and response.json()=={'accepted':1}


def test_optin_normal_and_control_message_maps(api):
    incoming=item('ordinary-id','ordinary')
    response=api.client.post('/api/v1/articles/batch?resolve_ids=true',headers=api.headers,json={'items':[incoming]})
    assert response.status_code==200 and response.json()=={'accepted':1,'idMap':{'ordinary-id':'ordinary-id'}}
    response=api.client.post('/api/v1/articles/batch?resolve_ids=true',headers=api.headers,
                             json={'items':[item('status-control','demo-health',tags=['health_status'])]})
    assert response.status_code==200 and response.json()=={'accepted':0,'idMap':{}}
    with api.factory() as db:assert db.get(Article,'status-control') is None
