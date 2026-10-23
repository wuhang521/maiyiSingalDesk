from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from .auth import require_api_key
from .database import get_db
from .models import Article, ArticleUserState, SmartChannel, utcnow
from .schemas import (
    ArticleBatchRequest,
    ArticleInput,
    ArticleList,
    ArticleRead,
    ArticleStatePatch,
    ArticleSyncInput,
    ArticleSyncRequest,
    SmartChannelRead,
    SmartChannelUpsert,
)


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
TRASH_RETENTION_DAYS = 7


def milliseconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.replace(tzinfo=timezone.utc).timestamp() * 1000)


def from_milliseconds(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, timezone.utc).replace(tzinfo=None)


def current_milliseconds() -> int:
    return int(utcnow().timestamp() * 1000)


def get_operator(
    x_signaldesk_operator: str = Header(default=""),
) -> str:
    name = unquote(x_signaldesk_operator).strip()
    if not name:
        raise HTTPException(status_code=400, detail="请在设置中填写当前操作人")
    return name


def article_hash(item: ArticleInput) -> str:
    content = "\n".join((item.title, item.body, item.source, item.clickUrl or ""))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def upsert_article(db: Session, item: ArticleInput) -> Article:
    article = db.get(Article, item.id)
    content_hash = article_hash(item)
    if article is None:
        article = db.scalar(
            select(Article)
            .where(Article.content_hash == content_hash)
            .order_by(Article.created_at)
            .limit(1)
        )
    matched_existing_content = article is not None and article.id != item.id
    values = {
        "channel_id": item.channelId,
        "title": item.title,
        "body": item.body,
        "source": item.source,
        "click_url": item.clickUrl,
        "tags": item.tags,
        "priority": item.priority,
        "published_at": from_milliseconds(item.timestamp),
        "content_hash": content_hash,
    }
    if matched_existing_content and not item.id.startswith("daily:"):
        values["channel_id"] = article.channel_id
        values["published_at"] = article.published_at
    if article is None:
        article = Article(id=item.id, **values)
        db.add(article)
    else:
        for field, value in values.items():
            setattr(article, field, value)
    return article


def apply_state(
    state: ArticleUserState,
    item: ArticleSyncInput,
    state_updated_at_ms: int,
) -> None:
    state.read = item.read
    state.saved = item.saved
    state.feedback = item.feedback
    if item.purged:
        state.permanently_deleted_at = utcnow()
    elif item.deletedAt is not None:
        state.deleted_at = from_milliseconds(item.deletedAt)
        state.purge_at = state.deleted_at + timedelta(days=TRASH_RETENTION_DAYS)
        state.permanently_deleted_at = None
    else:
        state.deleted_at = None
        state.purge_at = None
        state.permanently_deleted_at = None
    state.state_updated_at_ms = state_updated_at_ms


def purge_expired_article_states(db: Session) -> int:
    now = utcnow()
    result = db.execute(
        update(ArticleUserState)
        .where(
            ArticleUserState.deleted_at.is_not(None),
            ArticleUserState.purge_at <= now,
            ArticleUserState.permanently_deleted_at.is_(None),
        )
        .values(permanently_deleted_at=now, updated_at=now)
    )
    return result.rowcount or 0


def article_read(article: Article, state: ArticleUserState | None) -> ArticleRead:
    return ArticleRead(
        id=article.id,
        channelId=article.channel_id,
        title=article.title,
        body=article.body,
        timestamp=milliseconds(article.published_at) or 0,
        source=article.source,
        clickUrl=article.click_url,
        tags=article.tags or [],
        priority=article.priority,
        read=state.read if state else False,
        saved=state.saved if state else False,
        feedback=state.feedback if state else None,
        deletedAt=milliseconds(state.deleted_at) if state else None,
        purged=bool(state and state.permanently_deleted_at),
        stateUpdatedAt=state.state_updated_at_ms if state else 0,
    )


def channel_read(channel: SmartChannel) -> SmartChannelRead:
    return SmartChannelRead(
        id=channel.id,
        name=channel.name,
        description=channel.description,
        prompt=channel.prompt,
        topic=channel.topic,
        server=channel.server,
        schedule=channel.schedule,
        maxItems=channel.max_items,
        includeTerms=channel.include_terms or [],
        excludeTerms=channel.exclude_terms or [],
        color=channel.color,
        state=channel.state,
        createdAt=milliseconds(channel.created_at) or 0,
        updatedAt=milliseconds(channel.updated_at) or 0,
    )


@router.get("/channels", response_model=list[SmartChannelRead])
def list_channels(db: Session = Depends(get_db)) -> list[SmartChannelRead]:
    channels = db.scalars(select(SmartChannel).order_by(SmartChannel.name)).all()
    return [channel_read(channel) for channel in channels]


@router.post("/channels")
def upsert_channel(
    payload: SmartChannelUpsert,
    preserve_connection: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    channel = db.get(SmartChannel, payload.id)
    values = {
        "name": payload.name.strip(),
        "description": payload.description,
        "prompt": payload.prompt,
        "topic": payload.topic.strip(),
        "server": payload.server.strip(),
        "schedule": payload.schedule,
        "max_items": payload.maxItems,
        "include_terms": payload.includeTerms,
        "exclude_terms": payload.excludeTerms,
        "color": payload.color,
        "state": payload.state,
    }
    if channel is None:
        channel = SmartChannel(id=payload.id, **values)
        db.add(channel)
    else:
        if preserve_connection:
            values["topic"] = channel.topic
            values["server"] = channel.server
            values["schedule"] = channel.schedule
            values["state"] = channel.state
        for field, value in values.items():
            setattr(channel, field, value)
    db.commit()
    return {"id": channel.id}


@router.delete("/channels/{channel_id}", status_code=204, response_class=Response)
def delete_channel(channel_id: str, db: Session = Depends(get_db)) -> Response:
    channel = db.get(SmartChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="频道不存在")
    db.delete(channel)
    db.commit()
    return Response(status_code=204)


@router.post("/articles/batch")
def batch_articles(payload: ArticleBatchRequest, db: Session = Depends(get_db)) -> dict[str, int]:
    unique_items = {item.id: item for item in payload.items}
    for item in unique_items.values():
        upsert_article(db, item)
        db.flush()
    db.commit()
    return {"accepted": len(unique_items)}


@router.post("/articles/sync")
def sync_articles(
    payload: ArticleSyncRequest,
    operator: str = Depends(get_operator),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    accepted = 0
    now_ms = current_milliseconds()
    for item in {item.id: item for item in payload.items}.values():
        article = upsert_article(db, item)
        db.flush()
        state = db.get(ArticleUserState, (operator, article.id))
        incoming_ms = item.stateUpdatedAt or now_ms
        if state is None:
            state = ArticleUserState(operator_name=operator, article_id=article.id)
            db.add(state)
            apply_state(state, item, incoming_ms)
            accepted += 1
        elif item.stateUpdatedAt and item.stateUpdatedAt > state.state_updated_at_ms:
            apply_state(state, item, incoming_ms)
            accepted += 1
    db.commit()
    return {"accepted": accepted}


@router.get("/articles", response_model=ArticleList)
def list_articles(
    state: str = Query(default="active", pattern="^(active|trash|all)$"),
    limit: int = Query(default=1000, ge=1, le=2000),
    operator: str = Depends(get_operator),
    db: Session = Depends(get_db),
) -> ArticleList:
    if purge_expired_article_states(db):
        db.commit()
    join_condition = and_(
        ArticleUserState.article_id == Article.id,
        ArticleUserState.operator_name == operator,
    )
    query = select(Article, ArticleUserState).outerjoin(ArticleUserState, join_condition)
    if state == "active":
        query = query.where(
            or_(ArticleUserState.operator_name.is_(None), ArticleUserState.deleted_at.is_(None)),
            or_(
                ArticleUserState.operator_name.is_(None),
                ArticleUserState.permanently_deleted_at.is_(None),
            ),
        )
    elif state == "trash":
        query = query.where(
            ArticleUserState.deleted_at.is_not(None),
            ArticleUserState.permanently_deleted_at.is_(None),
        )
    else:
        query = query.where(
            or_(
                ArticleUserState.operator_name.is_(None),
                ArticleUserState.permanently_deleted_at.is_(None),
            )
        )
    rows = db.execute(query.order_by(Article.published_at.desc()).limit(limit)).all()
    return ArticleList(items=[article_read(article, user_state) for article, user_state in rows], total=len(rows))


@router.patch("/articles/{article_id}/state", response_model=ArticleRead)
def update_article_state(
    article_id: str,
    payload: ArticleStatePatch,
    operator: str = Depends(get_operator),
    db: Session = Depends(get_db),
) -> ArticleRead:
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    state = db.get(ArticleUserState, (operator, article_id))
    if state is None:
        state = ArticleUserState(
            operator_name=operator,
            article_id=article_id,
            state_updated_at_ms=0,
        )
        db.add(state)

    incoming_ms = payload.stateUpdatedAt or current_milliseconds()
    if payload.stateUpdatedAt and payload.stateUpdatedAt <= (state.state_updated_at_ms or 0):
        return article_read(article, state)
    if payload.read is not None:
        state.read = payload.read
    if payload.saved is not None:
        state.saved = payload.saved
    if "feedback" in payload.model_fields_set:
        state.feedback = payload.feedback
    if payload.action == "delete":
        state.deleted_at = utcnow()
        state.purge_at = state.deleted_at + timedelta(days=TRASH_RETENTION_DAYS)
        state.permanently_deleted_at = None
    elif payload.action == "restore":
        state.deleted_at = None
        state.purge_at = None
        state.permanently_deleted_at = None
    elif payload.action == "permanent":
        state.permanently_deleted_at = utcnow()
    state.state_updated_at_ms = incoming_ms
    db.commit()
    return article_read(article, state)
