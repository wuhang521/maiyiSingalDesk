"""Optional correlation span; missing trace never proves zero charge or resends HTTP."""
from contextlib import contextmanager
from contextvars import ContextVar
import re
import uuid
from .database import SessionLocal
from .knowledge_models import KnowledgeCloudUsage, KnowledgeProviderTrace
from sqlalchemy.orm import sessionmaker
_span = ContextVar("knowledge_provider_trace", default=None)

@contextmanager
def span(usage_id, factory=SessionLocal, *, db=None):
    if db is not None: factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    token = _span.set((usage_id, factory))
    try: yield
    finally: _span.reset(token)

def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value) else ""

def record(provider, response, payload=None):
    scope = _span.get()
    if scope is None or provider not in {"ark", "deepseek", "volc_asr"}: return
    usage_id, factory = scope
    request_id = next((identifier(response.headers.get(key)) for key in ("X-Request-ID", "X-Tt-Logid", "X-Api-Request-Id") if identifier(response.headers.get(key))), "")
    response_id = identifier(payload.get("id")) if isinstance(payload, dict) else ""
    raw = payload.get("usage", {}) if isinstance(payload, dict) else {}
    usage = {k: v for k, v in raw.items() if k in {"prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"} and type(v) is int and 0 <= v <= 1000000000} if isinstance(raw, dict) else {}
    try:
        with factory() as db:
            if not db.get(KnowledgeCloudUsage, usage_id): return
            db.add(KnowledgeProviderTrace(id=uuid.uuid4().hex, usage_id=usage_id, provider=provider, request_id=request_id,
                response_id=response_id, http_status=response.status_code, usage=usage)); db.commit()
    except Exception: pass  # Ledger stays reserved/unknown; do not log bodies or retry inference.

def traces(db, usage_ids):
    from sqlalchemy import select
    result = {identity: [] for identity in usage_ids}
    if not result: return result
    for row in db.scalars(select(KnowledgeProviderTrace).where(KnowledgeProviderTrace.usage_id.in_(usage_ids)).order_by(KnowledgeProviderTrace.created_at).limit(1000)):
        result[row.usage_id].append(dict(provider=row.provider, request_id=row.request_id, response_id=row.response_id,
            http_status=row.http_status, usage=row.usage, created_at=row.created_at))
    return result
