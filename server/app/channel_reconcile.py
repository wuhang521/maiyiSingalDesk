"""Transactional, reversible reconciliation of the two confirmed health IDs.

Snapshots contain private source/state values and must remain on the managed
server in a restricted backup directory. Public evidence uses fingerprints only.
"""
from datetime import datetime
import hashlib
import json

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .channel_aliases import SmartChannelAlias
from .models import Article, ArticleUserState, SmartChannel

CANONICAL = 'health'
LEGACY = 'demo-health'


def encode(value):
    if isinstance(value, datetime):
        return {'$datetime': value.isoformat(timespec='microseconds')}
    return value


def decode(value):
    if isinstance(value, dict) and set(value) == {'$datetime'}:
        return datetime.fromisoformat(value['$datetime'])
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def rows(db, model, condition=None, order=()):
    statement = select(model.__table__)
    if condition is not None:
        statement = statement.where(condition)
    if order:
        statement = statement.order_by(*order)
    return [{key: encode(value) for key, value in row.items()}
            for row in db.execute(statement).mappings()]


def capture(db: Session):
    articles = rows(db, Article, Article.channel_id.in_([CANONICAL, LEGACY]), [Article.id])
    ids = [row['id'] for row in articles]
    return {
        'articles': articles,
        'states': rows(db, ArticleUserState, ArticleUserState.article_id.in_(ids),
                       [ArticleUserState.operator_name, ArticleUserState.article_id]),
        'channels': rows(db, SmartChannel, SmartChannel.id.in_([CANONICAL, LEGACY]), [SmartChannel.id]),
        'aliases': rows(db, SmartChannelAlias,
                        (SmartChannelAlias.alias_id == LEGACY) | (SmartChannelAlias.canonical_id == CANONICAL),
                        [SmartChannelAlias.alias_id]),
        'other_channels_digest': digest(rows(db, SmartChannel,
                                            ~SmartChannel.id.in_([CANONICAL, LEGACY]), [SmartChannel.id])),
    }


def fingerprints(snapshot):
    return {
        'article_count': len(snapshot['articles']),
        'state_count': len(snapshot['states']),
        'article_ids_sha256': digest([row['id'] for row in snapshot['articles']]),
        'article_content_and_times_sha256': digest([
            {key: value for key, value in row.items() if key != 'channel_id'}
            for row in snapshot['articles']]),
        'all_personal_states_sha256': digest(snapshot['states']),
        'other_channels_sha256': snapshot['other_channels_digest'],
    }


def reconcile(db: Session, topic: str, server: str, *, fail_at=None):
    """Caller owns commit/rollback and maintenance window; no article is deleted."""
    if not topic.strip() or not server.startswith(('https://', 'http://')):
        raise ValueError('Verified publisher target required')
    # Alias metadata is static after this migration. The API locks the canonical
    # before managed writes; a maintenance window also excludes pre-fix writers.
    locked = list(db.scalars(select(SmartChannel)
                            .where(SmartChannel.id.in_([CANONICAL, LEGACY]))
                            .order_by(SmartChannel.id).with_for_update()
                            .execution_options(populate_existing=True)))
    by_id = {row.id: row for row in locked}
    if CANONICAL not in by_id:
        raise ValueError('Expected existing health channel is missing')
    before = capture(db)
    alias = db.get(SmartChannelAlias, LEGACY)
    if alias and alias.canonical_id != CANONICAL:
        raise ValueError('Existing alias points elsewhere')
    if not alias:
        db.add(SmartChannelAlias(alias_id=LEGACY, canonical_id=CANONICAL))
    channel = by_id[CANONICAL]
    channel.topic = topic.strip(); channel.server = server.rstrip('/')
    channel.schedule = '09:00'; channel.state = 'active'
    # Keep current health prompt/rules/max_items/color and timestamps on articles.
    db.flush()
    if fail_at == 'connection':
        raise RuntimeError('Synthetic migration failure after connection')
    moved = db.execute(update(Article).where(Article.channel_id == LEGACY)
                       .values(channel_id=CANONICAL, updated_at=Article.updated_at)
                       .execution_options(synchronize_session=False)).rowcount
    if fail_at == 'articles':
        raise RuntimeError('Synthetic migration failure after articles')
    if LEGACY in by_id:
        db.delete(by_id[LEGACY])
    db.flush()
    after = capture(db)
    if fingerprints(before) != fingerprints(after):
        raise RuntimeError('Article, personal state or unrelated channel changed')
    if any(row['channel_id'] != CANONICAL for row in after['articles']):
        raise RuntimeError('Unreconciled article remains')
    return {'passed': True, 'moved_articles': moved, 'canonical': CANONICAL,
            'alias': LEGACY, 'fingerprints': fingerprints(after),
            'before_digest': digest(before), 'after_digest': digest(after)}


def restore(db: Session, before, expected_after_digest: str):
    """Compensation only while writes are stopped and exact after-image matches."""
    list(db.scalars(select(SmartChannel).where(SmartChannel.id.in_([CANONICAL, LEGACY]))
                    .order_by(SmartChannel.id).with_for_update()))
    if digest(capture(db)) != expected_after_digest:
        raise ValueError('Current data differs; refusing to overwrite newer work')
    # Recreate/update the two channel rows with their exact original values.
    for row in before['channels']:
        values = {key: decode(value) for key, value in row.items()}
        current = db.get(SmartChannel, values['id'])
        if current is None:
            db.add(SmartChannel(**values))
        else:
            db.execute(update(SmartChannel).where(SmartChannel.id == values['id']).values(**values))
    for row in before['articles']:
        db.execute(update(Article).where(Article.id == row['id'])
                   .values(channel_id=row['channel_id'], updated_at=decode(row['updated_at'])))
    db.execute(delete(SmartChannelAlias).where(
        (SmartChannelAlias.alias_id == LEGACY) | (SmartChannelAlias.canonical_id == CANONICAL)))
    for row in before['aliases']:
        db.add(SmartChannelAlias(**row))
    db.flush(); db.expire_all()
    if digest(capture(db)) != digest(before):
        raise RuntimeError('Restored rows differ from original snapshot')
