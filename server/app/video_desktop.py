"""Video-only local service bound to an upstream-verified desktop identity.

The bootstrap envelope arrives over the Electron parent's private stdin pipe.
It contains no upstream bearer token and cannot create a local login session.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit


LEASE_SECONDS = 300
SERVICE = "maiyi-desktop-video"
MODEL_KEYS = {"prompt_api_key", "prompt_base_url", "prompt_model", "seedance_api_key", "seedance_base_url", "seedance_model"}
RUNTIME_KEYS = {"TASK_VIDEO_PAID_ENABLED", "TASK_VIDEO_PYTHON", "TASK_VIDEO_FFMPEG", "TASK_VIDEO_PRICING_PROFILE",
    "TASK_VIDEO_SCENE_PROVIDER", "TASK_VIDEO_COST_REVIEW_REQUIRED", "TASK_VIDEO_VOICE_COST_PER_1000_CHARS",
    "TASK_VIDEO_SCENE_COST_PER_SECOND", "TASK_VIDEO_AVATAR_COST_PER_SECOND", "TASK_VIDEO_RUNNINGHUB_KEY_TYPE",
    "TASK_VIDEO_AVATAR_CURRENCY", "DASHSCOPE_API_KEY", "BL_KEY_FILE", "RUNNINGHUB_API_KEY", "RH_KEY_FILE"}


class DesktopConfigurationError(RuntimeError):
    pass


def _plain_path(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DesktopConfigurationError("Invalid desktop video directory")
    target = Path(value)
    if not target.is_absolute():
        raise DesktopConfigurationError("Desktop video directories must be absolute")
    for item in (target, *target.parents):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise DesktopConfigurationError("Linked desktop video directories are not allowed")
    return target.resolve()


def normalized_issuer(value: Any) -> str:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(c.isspace() for c in value)
                or (parsed.scheme == "http" and host not in {"127.0.0.1", "localhost"})):
            raise ValueError()
        port = parsed.port
        authority = host if port in (None, 443 if parsed.scheme == "https" else 80) else f"{host}:{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))
    except (ValueError, TypeError, AttributeError) as exc:
        raise DesktopConfigurationError("Invalid verified backend identity") from exc


@dataclass(frozen=True)
class DesktopConfiguration:
    data_dir: Path
    config_dir: Path
    identity: dict[str, Any]
    identity_hash: str
    capability_token: str
    control_token: str
    instance_id: str
    cors_origins: tuple[str, ...]
    settings: Any
    environment: dict[str, str]


def configure(envelope: dict, *, loaders: dict[str, Callable] | None = None,
              install_root: Path | None = None) -> DesktopConfiguration:
    """Read purpose-bound provider config; do not mutate settings or environment."""
    allowed = {"data_dir", "config_dir", "identity", "capability_token", "control_token", "instance_id", "cors_origins"}
    if not isinstance(envelope, dict) or set(envelope) != allowed:
        raise DesktopConfigurationError("Invalid desktop video bootstrap")
    identity = envelope.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"backend_url", "id", "username", "display_name"}:
        raise DesktopConfigurationError("Verified personal identity is required")
    if type(identity["id"]) is not int or identity["id"] <= 0:
        raise DesktopConfigurationError("Invalid verified account identifier")
    if any(not isinstance(identity[key], str) or not identity[key].strip() or len(identity[key]) > 80
           or any(c in identity[key] for c in "\r\n\x00") for key in ("username", "display_name")):
        raise DesktopConfigurationError("Invalid verified account profile")
    identity = {**identity, "backend_url": normalized_issuer(identity["backend_url"])}
    identity_hash = hashlib.sha256(f"{identity['backend_url']}\0{identity['id']}".encode()).hexdigest()
    tokens = [envelope[key] for key in ("capability_token", "control_token")]
    if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value) for value in tokens) or tokens[0] == tokens[1]:
        raise DesktopConfigurationError("Distinct private desktop capabilities are required")
    instance = envelope["instance_id"]
    if not isinstance(instance, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", instance):
        raise DesktopConfigurationError("Invalid desktop video instance")
    origins = envelope["cors_origins"]
    if not isinstance(origins, list) or len(origins) > 5 or any(not isinstance(origin, str) or origin == "*"
            or (origin != "null" and not re.fullmatch(r"http://(?:localhost|127\.0\.0\.1):[0-9]{1,5}", origin)) for origin in origins):
        raise DesktopConfigurationError("Invalid desktop video origins")
    data_dir, config_dir = _plain_path(envelope["data_dir"]), _plain_path(envelope["config_dir"])
    if data_dir == config_dir or (data_dir / "trial.db").exists():
        raise DesktopConfigurationError("The formal desktop cannot use trial data")
    path = config_dir / "video-config.json"
    _plain_path(str(path))
    try:
        values = json.loads(path.read_text("utf-8-sig")) if path.is_file() else {}
    except (OSError, ValueError) as exc:
        raise DesktopConfigurationError("Cannot read desktop video provider configuration") from exc
    if not isinstance(values, dict) or set(values) - MODEL_KEYS - RUNTIME_KEYS or any(not isinstance(value, str) for value in values.values()):
        raise DesktopConfigurationError("Unknown desktop video provider configuration")
    if loaders is None:
        from .video_hermes_config import load_hermes_video_config
        from .video_hermes_scene_config import load_hermes_scene_config
        from .video_hermes_voice_config import load_hermes_voice_config
        loaders = {"primary": load_hermes_video_config, "scene": load_hermes_scene_config, "voice": load_hermes_voice_config}
    model = {key: value for key, value in values.items() if key in MODEL_KEYS and value.strip()}
    primary = {} if model.get("prompt_model") else loaders["primary"](config_dir / "hermes-provider-config.dpapi")
    if primary:
        model = {key: value for key, value in model.items() if not key.startswith("prompt_")}
        model.update(primary)
    local_scene = bool(model.get("seedance_api_key"))
    inherited_scene = {} if local_scene else loaders["scene"](config_dir / "hermes-scene-config.dpapi")
    model.update(inherited_scene)
    scene_source = "local" if local_scene else "hermes" if inherited_scene else "unconfigured"
    environment = {key: value for key, value in values.items() if key in RUNTIME_KEYS}
    scene_provider = environment.get("TASK_VIDEO_SCENE_PROVIDER", "seedance").strip()
    if scene_provider not in {"seedance", "runninghub"}:
        raise DesktopConfigurationError("Unsupported ordinary-video provider; no automatic fallback is allowed")
    voice = {key: values[key] for key in ("DASHSCOPE_API_KEY", "BL_KEY_FILE") if values.get(key, "").strip()}
    if not voice:
        voice = loaders["voice"](config_dir / "hermes-voice-config.dpapi")
    environment.update(voice)
    root = install_root or Path(__file__).resolve().parents[2]
    environment.update({"TASK_VIDEO_SCENE_PROVIDER": scene_provider, "TASK_VIDEO_COST_REVIEW_REQUIRED": "0",
        "TASK_VIDEO_SKILL_DIR": str(root / "hermes-skill/oldx-avatar"),
        "TASK_VIDEO_STORAGE_DIR": str(data_dir / "video-files"),
        "TASK_VIDEO_VOICE_CREDENTIAL_SOURCE": "local" if values.get("DASHSCOPE_API_KEY") or values.get("BL_KEY_FILE") else "hermes" if voice else "unconfigured",
        "TASK_VIDEO_SCENE_CREDENTIAL_SOURCE": "runninghub_shared_key" if scene_provider == "runninghub" else scene_source})
    environment.setdefault("TASK_VIDEO_PAID_ENABLED", "0")
    environment.setdefault("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "SHARED")
    environment.setdefault("TASK_VIDEO_AVATAR_CURRENCY", "CNY")
    from .config import Settings
    # Explicit defaults prevent unrelated inherited TASK_* values entering Settings.
    defaults = {key: field.default for key, field in Settings.model_fields.items()}
    defaults.update(model, database_url=f"sqlite:///{(data_dir / 'video.db').as_posix()}",
        app_name="迈医协同 · 本机视频服务", api_key="", video_storage_dir=str(data_dir / "video-files"),
        knowledge_storage_dir=str(data_dir / "unused-knowledge"), video_planner_source="hermes" if primary else "local", cors_origins=",".join(origins))
    settings = Settings(_env_file=None, **defaults)
    return DesktopConfiguration(data_dir, config_dir, identity, identity_hash, *tokens, instance, tuple(origins), settings, environment)


def activate(configuration: DesktopConfiguration) -> None:
    """Runner-only process configuration, before importing database/router modules."""
    for key in list(os.environ):
        if key.startswith(("TASK_", "ARK_", "TTS_", "SEEDANCE_", "DASHSCOPE_", "RUNNINGHUB_", "OLDX_")) or key in {"BL_KEY_FILE", "RH_KEY_FILE"}:
            del os.environ[key]
    os.environ.update(configuration.environment)
    os.environ.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    from . import config
    config.get_settings = lambda: configuration.settings


def _bind_identity(configuration: DesktopConfiguration) -> None:
    directory = configuration.data_dir
    directory.mkdir(parents=True, exist_ok=True)
    _plain_path(str(directory))
    marker = directory / "desktop-video-identity.json"
    _plain_path(str(marker))
    expected = {"service": SERVICE, "schema": 1, "identity_hash": configuration.identity_hash,
                "issuer": configuration.identity["backend_url"], "subject": configuration.identity["id"]}
    if marker.exists():
        if json.loads(marker.read_text("utf-8")) != expected:
            raise DesktopConfigurationError("Desktop video data belongs to a different verified account")
    elif (directory / "video.db").exists():
        raise DesktopConfigurationError("Existing desktop video database has no verified owner")
    else:
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(expected, stream, ensure_ascii=False)


def create_app(configuration: DesktopConfiguration, *, clock: Callable[[], float] = time.monotonic,
               capabilities: Callable[[], dict] | None = None):
    """Build a private video-only app; no auth, administration, or task routers."""
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import Session, selectinload
    from .auth import AuthPrincipal, require_api_key
    from .database import Base, get_db
    from .models import Staff, UserAccount
    from .video_project_models import VideoProject, VideoProjectJob, VideoProjectReview
    from .video_projects import router

    _bind_identity(configuration)
    engine = create_engine(configuration.settings.database_url, connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine, tables=[Staff.__table__, UserAccount.__table__, VideoProject.__table__, VideoProjectReview.__table__, VideoProjectJob.__table__])
    with Session(engine) as db:
        existing = list(db.scalars(select(UserAccount)))
        if existing and (len(existing) != 1 or existing[0].id != configuration.identity["id"]):
            engine.dispose()
            raise DesktopConfigurationError("Unexpected account in desktop video database")
        user = existing[0] if existing else UserAccount(id=configuration.identity["id"],
            staff=Staff(name=configuration.identity["display_name"]), password_hash="external-identity:no-local-login", role="member")
        user.username = configuration.identity["username"]
        user.staff.name = configuration.identity["display_name"]
        user.enabled, user.staff.enabled = True, True
        db.add(user)
        db.commit()
    app = FastAPI(title="迈医协同 · 本机视频服务", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=list(configuration.cors_origins),
        allow_credentials=False, allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.state.engine, app.state.configuration = engine, configuration
    app.state.lease_deadline = clock() + LEASE_SECONDS
    def database():
        with Session(engine, expire_on_commit=False) as db:
            yield db
    def valid_token(header: str, expected: str):
        scheme, _, token = header.partition(" ")
        return scheme.casefold() == "bearer" and secrets.compare_digest(token.strip(), expected)
    def principal(authorization: str = Header(default=""), db=Depends(database)):
        if not valid_token(authorization, configuration.capability_token) or clock() >= app.state.lease_deadline:
            raise HTTPException(401, "本机视频会话已失效，请保持正式账号登录后重试")
        user = db.scalar(select(UserAccount).options(selectinload(UserAccount.staff)).where(UserAccount.id == configuration.identity["id"]))
        if user is None:
            raise HTTPException(401, "本机视频账号绑定已失效")
        return AuthPrincipal(user=user)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_api_key] = principal
    app.include_router(router)
    @app.post("/desktop/lease")
    def lease(authorization: str = Header(default="")):
        if not valid_token(authorization, configuration.control_token):
            raise HTTPException(401, "本机视频控制会话无效")
        app.state.lease_deadline = clock() + LEASE_SECONDS
        return {"service": SERVICE, "instance_id": configuration.instance_id, "lease_seconds": LEASE_SECONDS}
    @app.get("/desktop/status")
    def status(_principal=Depends(principal)):
        # This handshake authenticates the sidecar, not its media toolchain.
        # Cold FFmpeg discovery can start another interpreter and take >10s.
        # Full readiness belongs to /api/v1/video/projects/capabilities.
        return {"service": SERVICE, "instance_id": configuration.instance_id, "identity_hash": configuration.identity_hash,
                "cost_review_required": False, "scene_provider": configuration.environment["TASK_VIDEO_SCENE_PROVIDER"],
                "provider_ready": {}, "capabilities_checked": False,
                "lease_seconds_remaining": max(0, int(app.state.lease_deadline - clock()))}
    @app.get("/desktop/update-safety")
    def update_safety(authorization: str = Header(default="")):
        # The parent-only control capability remains usable when the view lease
        # expires. It reveals no project content or provider credentials.
        if not valid_token(authorization, configuration.control_token):
            raise HTTPException(401, "本机视频控制会话无效")
        with Session(engine) as db:
            active = db.scalar(select(VideoProjectJob.id).where(
                VideoProjectJob.status.in_(("queued", "running", "submitting"))).limit(1))
        return {"safe_to_restart": active is None}
    return app
