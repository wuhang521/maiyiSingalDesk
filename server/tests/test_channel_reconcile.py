from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.channel_aliases import SmartChannelAlias
from app.channel_reconcile import capture, digest, fingerprints, reconcile, restore
from app.database import Base
from app.models import Article, ArticleUserState, SmartChannel


@pytest.fixture
def db(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'merge.db'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with factory() as s:
        for channel in ['health','demo-health','other']:
            s.add(SmartChannel(id=channel,name=channel,topic='old-topic' if channel=='demo-health' else '',
                               state='draft',schedule='09:00',max_items=100 if channel=='health' else 20))
        for i,channel in enumerate(['health','demo-health','demo-health']):
            when=datetime(2026,10,25,1,2,3,123456+i)
            s.add(Article(id=f'article-{i}',channel_id=channel,title='Title'+str(i),body='Immutable'+str(i),
                          source='Synthetic',tags=['test'],priority=3,published_at=when,created_at=when,
                          updated_at=when,content_hash=str(i)*64))
        s.flush()
        for operator in ['alice','bob']:
            for i in range(3):
                when=datetime(2026,10,26,1,2,3,654321+i)
                s.add(ArticleUserState(operator_name=operator,article_id=f'article-{i}',read=i==0,
                       saved=i==1,feedback='relevant' if i==0 else None,
                       deleted_at=when if i==2 else None,purge_at=when+timedelta(days=7) if i==2 else None,
                       permanently_deleted_at=when if operator=='bob' and i==2 else None,
                       state_updated_at_ms=1786400000123+i,updated_at=when))
        s.commit();yield s
    engine.dispose()


def test_merge_preserves_all_articles_states_and_other_channels(db):
    before=capture(db);r=reconcile(db,'verified-target','https://push.example.test');db.commit()
    after=capture(db)
    assert r['moved_articles']==2 and fingerprints(after)==fingerprints(before)
    assert [c['id'] for c in after['channels']]==['health']
    assert all(a['channel_id']=='health' for a in after['articles'])
    assert db.get(SmartChannel,'health').max_items==100
    assert db.get(SmartChannelAlias,'demo-health').canonical_id=='health'


def test_repeated_merge_keeps_fingerprint_and_does_not_duplicate(db):
    reconcile(db,'verified-target','https://push.example.test');db.commit();before=capture(db)
    r=reconcile(db,'verified-target','https://push.example.test');db.commit()
    assert r['moved_articles']==0 and digest(capture(db))==digest(before)


@pytest.mark.parametrize('phase',['connection','articles'])
def test_failure_rolls_back_complete_change(db,phase):
    before=capture(db)
    with pytest.raises(RuntimeError):reconcile(db,'verified-target','https://push.example.test',fail_at=phase)
    db.rollback();db.expire_all();assert capture(db)==before


def test_restore_exact_before_image_including_microseconds(db):
    before=capture(db);r=reconcile(db,'verified-target','https://push.example.test');db.commit()
    restore(db,before,r['after_digest']);db.commit();assert capture(db)==before


def test_restore_refuses_to_overwrite_later_personal_work(db):
    before=capture(db);r=reconcile(db,'verified-target','https://push.example.test');db.commit()
    state=db.get(ArticleUserState,('alice','article-0'));state.saved=True;db.commit()
    with pytest.raises(ValueError,match='refusing'):restore(db,before,r['after_digest'])
    db.rollback();assert db.get(ArticleUserState,('alice','article-0')).saved is True


def test_wrong_existing_alias_is_not_overwritten(db):
    db.add(SmartChannelAlias(alias_id='demo-health',canonical_id='other'));db.commit()
    before=capture(db)
    with pytest.raises(ValueError,match='elsewhere'):reconcile(db,'verified-target','https://push.example.test')
    db.rollback();assert capture(db)==before


def test_missing_target_is_not_guessed(db):
    with pytest.raises(ValueError):reconcile(db,'','https://push.example.test')
