"""Disposable loopback API/SQLite fixture for the packaged health-channel E2E.

This is a preview harness, not a production entry point. All rows are synthetic;
cloud features are disabled and even DNS/connect attempts outside loopback fail.
"""
from pathlib import Path
import hashlib
import json
import os
import secrets
import socket
import sys
from urllib.parse import urlparse

run = Path(os.environ.get("HEALTH_E2E_RUN_DIR", "")).resolve()
database = os.environ.get("TASK_DATABASE_URL", "")
if not run.name.startswith("run-") or run.parent.name != "channel-e2e" or not run.is_dir():
    raise RuntimeError("Requires a newly created isolated channel-e2e/run-* directory")
expected_db = run / "health-channel-e2e.db"
if database != "sqlite:///" + expected_db.as_posix() or expected_db.exists():
    raise RuntimeError("Refusing a non-disposable or existing database")
ntfy = os.environ.get("HEALTH_E2E_NTFY_URL", "")
parsed = urlparse(ntfy)
if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path:
    raise RuntimeError("Requires the dedicated loopback ntfy stub")
diagnostic_key = os.environ.get("HEALTH_E2E_DIAGNOSTIC_KEY", "")
if len(diagnostic_key) < 24 or os.environ.get("TASK_KNOWLEDGE_ASR_ENABLED") != "false":
    raise RuntimeError("Missing preview isolation settings")
os.chdir(run)  # Settings must never discover a repository/user .env file.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

external_attempts = []
real_connect, real_connect_ex, real_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo


def check_host(host):
    if host not in {"127.0.0.1", "::1", "localhost", None}:
        external_attempts.append(str(host)[:120])
        raise OSError("External networking is disabled in the isolated preview")


def guarded_connect(sock, address):
    if isinstance(address, tuple):
        check_host(address[0])
    return real_connect(sock, address)


def guarded_connect_ex(sock, address):
    if isinstance(address, tuple):
        check_host(address[0])
    return real_connect_ex(sock, address)


def guarded_getaddrinfo(host, *args, **kwargs):
    check_host(host)
    return real_getaddrinfo(host, *args, **kwargs)


socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = guarded_connect, guarded_connect_ex, guarded_getaddrinfo

from fastapi import Header, HTTPException
from sqlalchemy import delete, select
from app.main import app
from app.auth import hash_password
from app.channel_aliases import SmartChannelAlias
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Article, ArticleUserState, SmartChannel, Staff, UserAccount, UserSession, utcnow
from app import health_relay
from datetime import timedelta, timezone
import uvicorn
import httpx

# The production relay keeps its HTTPS-only fixed-config guard. This synthetic
# adapter alone maps that exact logical request to our actual loopback HTTP stub.
logical_server, logical_topic = "https://ntfy.synthetic.test", "synthetic-health"
health_relay.Config.load = lambda path: health_relay.Config(server=logical_server, topic=logical_topic, token="synthetic-upstream-token")


class LoopbackUpstream(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        if request.url.scheme != "https" or request.url.host != "ntfy.synthetic.test" or request.url.path != "/synthetic-health/json":
            raise RuntimeError("Unexpected synthetic upstream")
        target = httpx.URL(ntfy + "/synthetic-health/json").copy_with(query=request.url.query)
        translated = httpx.Request(request.method, target, headers=request.headers)
        return await self.inner.handle_async_request(translated)

    async def aclose(self):
        await self.inner.aclose()


health_relay.new_upstream_client = lambda: httpx.AsyncClient(transport=LoopbackUpstream(), timeout=httpx.Timeout(connect=5, read=90, write=5, pool=5), trust_env=False, follow_redirects=False)

settings = get_settings()
for name in ("knowledge_asr_enabled", "knowledge_cloud_asr_enabled", "knowledge_ocr_enabled", "knowledge_cloud_qa_enabled",
             "knowledge_semantic_enabled", "knowledge_organization_enabled", "knowledge_dispatch_enabled", "knowledge_video_ocr_enabled"):
    setattr(settings, name, False)
for name in ("prompt_api_key", "knowledge_qwen_api_key", "knowledge_cloud_asr_api_key", "knowledge_gateway_secret", "hermes_webhook_url",
             "seedance_api_key", "tts_access_token"):
    setattr(settings, name, "")

Base.metadata.create_all(engine)
now = utcnow()
timestamp = int(now.replace(tzinfo=timezone.utc).timestamp() * 1000)
operator = "健康频道隔离测试"
fixtures = []
for item_id, title in [("health-original", "合成：保留阅读与收藏"), ("daily:demo-health:original-id", "合成：保留旧文章编号"), ("health-trash", "合成：保留回收站")]:
    fixtures.append(dict(id=item_id, channelId="health", title=title, body="仅供程序验收的合成商业情报。" + title,
                         timestamp=timestamp, source="synthetic-local", tags=["synthetic"], priority=3,
                         read=True, saved=True, feedback="useful", stateUpdatedAt=timestamp))
with SessionLocal() as db:
    db.add(UserAccount(username="preview-health", staff=Staff(name=operator), role="member", password_hash=hash_password("synthetic-health-only-password")))
    db.add(SmartChannel(id="health", name="健康管理商业情报", description="由每日情报系统自动入库", prompt="仅供程序验收的合成规则",
                        topic=logical_topic, server=logical_server, schedule="09:00", max_items=100, include_terms=["商业情报"],
                        exclude_terms=["个人养生"], color="green", state="active"))
    db.add(SmartChannelAlias(alias_id="demo-health", canonical_id="health"))
    for item in fixtures:
        digest = hashlib.sha256("\n".join((item["title"], item["body"], item["source"], "")).encode()).hexdigest()
        db.add(Article(id=item["id"], channel_id="health", title=item["title"], body=item["body"], source=item["source"],
                       tags=item["tags"], priority=3, published_at=now, content_hash=digest))
    db.flush()
    for item in fixtures:
        deleted = now if item["id"] == "health-trash" else None
        db.add(ArticleUserState(operator_name=operator, article_id=item["id"], read=True, saved=True, feedback="useful",
                                deleted_at=deleted, purge_at=now + timedelta(days=7) if deleted else None, state_updated_at_ms=timestamp))
    db.commit()

inbound = []
inflight = 0
relay_requests = []
state_patches = []


@app.middleware("http")
async def audit(request, call_next):
    global inflight
    watched = request.method == "POST" and request.url.path in {"/api/v1/channels", "/api/v1/articles/batch", "/api/v1/articles/sync"}
    if watched:
        payload = json.loads(await request.body())
        inbound.append({"path": request.url.path, "resolve_ids": request.query_params.get("resolve_ids"), "channelIds": [payload.get("id")] if request.url.path.endswith("/channels")
                        else [item.get("channelId") for item in payload.get("items", [])],
                        "articleIds": [item.get("id") for item in payload.get("items", [])],
                        "keys": sorted(payload), "itemCount": len(payload.get("items", []))})
        inflight += 1
    try:
        response = await call_next(request)
        if request.url.path == "/api/v1/health-relay/health/json":
            relay_requests.append({"status": response.status_code, "poll": request.query_params.get("poll", "0"),
                                   "since": request.query_params.get("since", "10m"), "authenticated": bool(request.headers.get("authorization"))})
        if request.method == "PATCH" and request.url.path.endswith("/state"):
            state_patches.append({"articleId": request.path_params.get("article_id"), "status": response.status_code})
        return response
    finally:
        if watched:
            inflight -= 1


def diagnostic(x_preview_key: str = Header(default="")):
    if not secrets.compare_digest(x_preview_key, diagnostic_key):
        raise HTTPException(403, "Preview diagnostics only")


@app.get("/preview-health/fixture", dependencies=[])
def fixture(x_preview_key: str = Header(default="")):
    diagnostic(x_preview_key)
    return {"items": fixtures, "operator": operator, "timestamp": timestamp, "ntfy": ntfy, "logical_server": logical_server, "logical_topic": logical_topic}


@app.get("/preview-health/stats")
def stats(x_preview_key: str = Header(default="")):
    diagnostic(x_preview_key)
    with SessionLocal() as db:
        channels = [{"id": c.id, "topic": c.topic, "server": c.server, "schedule": c.schedule, "maxItems": c.max_items, "state": c.state}
                    for c in db.scalars(select(SmartChannel).order_by(SmartChannel.id)).all()]
        aliases = [{"alias": a.alias_id, "canonical": a.canonical_id} for a in db.scalars(select(SmartChannelAlias)).all()]
        articles = [{"id": a.id, "channelId": a.channel_id, "body_sha256": hashlib.sha256(a.body.encode()).hexdigest()}
                    for a in db.scalars(select(Article).order_by(Article.id)).all()]
        states = [{"articleId": s.article_id, "read": s.read, "saved": s.saved, "feedback": s.feedback,
                   "deleted": s.deleted_at is not None, "purged": s.permanently_deleted_at is not None, "stateUpdatedAt": s.state_updated_at_ms}
                  for s in db.scalars(select(ArticleUserState).where(ArticleUserState.operator_name == operator).order_by(ArticleUserState.article_id)).all()]
    return {"channels": channels, "aliases": aliases, "articles": articles, "states": states, "inbound": inbound, "relay_requests": relay_requests, "state_patches": state_patches,
            "inflight": inflight, "external_attempts": external_attempts, "synthetic_only": True}


@app.post("/preview-health/revoke")
def revoke(x_preview_key: str = Header(default="")):
    diagnostic(x_preview_key)
    with SessionLocal() as db:
        user_id = db.scalar(select(UserAccount.id).where(UserAccount.username == "preview-health"))
        db.execute(delete(UserSession).where(UserSession.user_id == user_id))
        db.commit()
    return {"synthetic_session_revoked": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
