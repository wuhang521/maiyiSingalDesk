"""Isolated, real local video backend. No provider simulations or production DB."""
import json
import os
from pathlib import Path
import secrets
import sys


def resolve_scene_settings(configuration, directory, *, loader=None):
    """Reuse only the dedicated video snapshot; explicit local values win."""
    if configuration.get("seedance_api_key", "").strip():
        return {key: configuration[key].strip() for key in ("seedance_api_key", "seedance_base_url", "seedance_model")
                if configuration.get(key, "").strip()}, "local"
    if loader is None:
        from app.video_hermes_scene_config import load_hermes_scene_config
        loader = load_hermes_scene_config
    inherited = loader(Path(directory) / "hermes-scene-config.dpapi")
    return inherited, "hermes" if inherited else "unconfigured"


def resolve_voice_environment(configuration, directory, *, loader=None):
    """Explicit local voice credentials win; otherwise use Hermes' voice snapshot."""
    local = {key: configuration[key].strip() for key in ("DASHSCOPE_API_KEY", "BL_KEY_FILE")
             if configuration.get(key, "").strip()}
    if local:
        return local, "local"
    if loader is None:
        from app.video_hermes_voice_config import load_hermes_voice_config
        loader = load_hermes_voice_config
    inherited = loader(Path(directory) / "hermes-voice-config.dpapi")
    return inherited, "hermes" if inherited else "unconfigured"


def main():
    server = Path(__file__).resolve().parents[1]
    requested_directory = Path(sys.argv[1])
    expected_directory = server.parent / "artifacts/video-local-trial"
    for candidate in (requested_directory, expected_directory, expected_directory.parent):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise RuntimeError("Refusing linked local video directories")
    directory = requested_directory.resolve()
    port = int(sys.argv[2])
    if directory != expected_directory.resolve() or not 1024 <= port <= 65535:
        raise RuntimeError("Requires the dedicated local video directory")
    request = json.loads(sys.stdin.readline(4096))
    password = request["password"]
    if not isinstance(password, str) or not 16 <= len(password) <= 128:
        raise RuntimeError("Invalid local password")
    database = directory / "trial.db"
    marker = directory / "database-owner.json"
    ownership = {"kind": "video-local-trial", "schema": "20261019_video_01"}
    if database.is_symlink() or marker.is_symlink():
        raise RuntimeError("Refusing symlinked trial data")
    if database.exists() and (not marker.is_file() or json.loads(marker.read_text("utf-8")) != ownership):
        raise RuntimeError("Unknown database; no data was reset")
    if not database.exists() and marker.exists():
        raise RuntimeError("Trial DB missing; existing accounts were not reset")
    fresh = not database.exists()
    # A separate private configuration is the only source of cloud settings.
    settings_file = directory / "video-config.json"
    if settings_file.is_symlink():
        raise RuntimeError("Refusing symlinked configuration")
    config_data = json.loads(settings_file.read_text("utf-8-sig")) if settings_file.exists() else {}
    model_keys = {"prompt_api_key", "prompt_base_url", "prompt_model", "seedance_api_key", "seedance_base_url", "seedance_model"}
    runtime_keys = {"TASK_VIDEO_PAID_ENABLED", "TASK_VIDEO_PYTHON", "TASK_VIDEO_FFMPEG", "TASK_VIDEO_PRICING_PROFILE", "TASK_VIDEO_SCENE_PROVIDER", "TASK_VIDEO_COST_REVIEW_REQUIRED", "TASK_VIDEO_VOICE_COST_PER_1000_CHARS", "TASK_VIDEO_SCENE_COST_PER_SECOND", "TASK_VIDEO_AVATAR_COST_PER_SECOND", "TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "TASK_VIDEO_AVATAR_CURRENCY", "DASHSCOPE_API_KEY", "BL_KEY_FILE", "RUNNINGHUB_API_KEY", "RH_KEY_FILE"}
    if not isinstance(config_data, dict) or set(config_data) - model_keys - runtime_keys or any(not isinstance(v, str) for v in config_data.values()):
        raise RuntimeError("Unknown configuration field; expected string values")
    for key in list(os.environ):
        if key.startswith(("TASK_", "ARK_", "TTS_", "SEEDANCE_", "DASHSCOPE_", "RUNNINGHUB_")) or key in {"BL_KEY_FILE", "RH_KEY_FILE"}:
            del os.environ[key]
    sys.path.insert(0, str(server))
    from app import config
    from app.video_hermes_config import load_hermes_video_config
    inherited_model = {} if config_data.get("prompt_model", "").strip() else load_hermes_video_config(directory / "hermes-provider-config.dpapi")
    model_fields = {k: v for k, v in config_data.items() if k in model_keys}
    uses_hermes = bool(inherited_model and not config_data.get("prompt_model", "").strip())
    if uses_hermes:
        # The blank starter template must not replace Hermes' base URL with Ark.
        model_fields = {k: v for k, v in model_fields.items() if not k.startswith("prompt_")}
        model_fields.update(inherited_model)
    scene_fields, scene_source = resolve_scene_settings(config_data, directory)
    model_fields.update(scene_fields)
    settings = config.Settings(_env_file=None,
        database_url=f"sqlite:///{database.as_posix()}",
        app_name="Hermes · 本机视频制作",
        api_key=secrets.token_urlsafe(40),
        knowledge_storage_dir=str(directory / "knowledge-files"),
        video_storage_dir=str(directory / "video-files"),
        video_planner_source="hermes" if uses_hermes else "local",
        cors_origins=f"http://127.0.0.1:{request['frontend_port']}",
        **model_fields)
    config.get_settings = lambda: settings
    for key in runtime_keys:
        if key in config_data:
            os.environ[key] = config_data[key]
    voice_environment, voice_source = resolve_voice_environment(config_data, directory)
    os.environ.update(voice_environment)
    os.environ["TASK_VIDEO_VOICE_CREDENTIAL_SOURCE"] = voice_source
    os.environ["TASK_VIDEO_SCENE_CREDENTIAL_SOURCE"] = scene_source
    scene_provider = os.environ.get("TASK_VIDEO_SCENE_PROVIDER", "seedance")
    public_scene_source = "runninghub_shared_key" if scene_provider == "runninghub" else scene_source
    public_scene_configured = bool(os.environ.get("RUNNINGHUB_API_KEY") or os.environ.get("RH_KEY_FILE")) if scene_provider == "runninghub" else bool(scene_fields)
    os.environ.setdefault("TASK_VIDEO_PAID_ENABLED", "0")
    os.environ["TASK_VIDEO_SKILL_DIR"] = str(server.parent / "hermes-skill/oldx-avatar")
    os.environ["TASK_VIDEO_STORAGE_DIR"] = str(directory / "video-files")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    from app.main import app
    from app.auth import hash_password
    from app.database import Base, SessionLocal, engine
    from app.models import Staff, UserAccount
    import uvicorn
    Base.metadata.create_all(engine)
    if fresh:
        with SessionLocal() as db:
            db.add(UserAccount(username="local-admin", staff=Staff(name="本机视频管理员"),
                               role="admin", password_hash=hash_password(password)))
            db.commit()
        marker.write_text(json.dumps(ownership), encoding="utf-8")

    @app.get("/local-trial/status")
    def local_status():
        return dict(mode="real-local-video", production_connected=False, provider_simulation=False,
                    planning_config_source=settings.video_planner_source,
                    voice_credential_source=voice_source,
                    voice_credentials_configured=bool(voice_environment),
                    scene_provider=scene_provider,
                    cost_review_required=os.environ.get("TASK_VIDEO_COST_REVIEW_REQUIRED", "1").lower() not in {"0", "false", "no"},
                    scene_credential_source=public_scene_source,
                    scene_credentials_configured=public_scene_configured,
                    paid_enabled=os.environ["TASK_VIDEO_PAID_ENABLED"].lower() in {"1", "true", "yes"},
                    pricing_profile=os.environ.get("TASK_VIDEO_PRICING_PROFILE", "manual"))

    @app.get("/local-trial/release")
    def release():
        return dict(tag_name="v0.5.3", assets=[], html_url=f"http://127.0.0.1:{port}/docs")

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
