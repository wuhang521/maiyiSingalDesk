"""Formal-account sidecar contracts with isolated SQLite and no cloud requests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.video_desktop import DesktopConfigurationError, configure, create_app, normalized_issuer


@pytest.fixture
def envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_DATABASE_URL", "sqlite:///:memory:")
    return {"data_dir": str(tmp_path / "account-video"), "config_dir": str(tmp_path / "provider-config"),
            "identity": {"backend_url": "https://example.test/task-api/", "id": 47,
                         "username": "verified-person", "display_name": "正式用户"},
            "capability_token": "capability_" + "A" * 40, "control_token": "control_" + "B" * 40,
            "instance_id": "instance-offline-01", "cors_origins": ["null", "http://127.0.0.1:5173"]}


def settings_for(envelope, **kwargs):
    return configure(envelope, loaders={name: lambda _path: {} for name in ("primary", "scene", "voice")}, **kwargs)


@pytest.fixture
def desktop(envelope):
    now = [1000.0]
    value = settings_for(envelope)
    app = create_app(value, clock=lambda: now[0],
                     capabilities=lambda: pytest.fail("Health checks must not probe provider or FFmpeg readiness"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield value, app, client, now
    app.state.engine.dispose()


def auth(value, control=False):
    return {"Authorization": "Bearer " + (value.control_token if control else value.capability_token)}


def test_update_guard_requires_parent_control_even_after_view_lease_expires(desktop):
    value, app, client, now = desktop
    route = "/desktop/update-safety"
    assert client.get(route).status_code == 401
    assert client.get(route, headers=auth(value)).status_code == 401
    now[0] += 1000
    assert client.get(route, headers=auth(value, control=True)).json() == {"safe_to_restart": True}


@pytest.mark.parametrize("status,safe", [("queued", False), ("running", False), ("submitting", False),
    ("succeeded", True), ("blocked", True), ("needs_attention", True), ("failed", True)])
def test_update_guard_protects_active_video_jobs_without_touching_projects(desktop, status, safe):
    from app.video_project_models import VideoProject, VideoProjectJob
    value, app, client, _now = desktop
    with Session(app.state.engine) as db:
        db.add(VideoProject(id="update-guard-project", owner_id=value.identity["id"], topic="offline fixture",
            mode="standard", platforms=[], revision=1, status="producing", plan={}, locked_fields=[]))
        db.flush()
        db.add(VideoProjectJob(id="update-guard-job", project_id="update-guard-project", revision=1, kind="render",
            status=status, idempotency_key="update-guard", request_hash="a" * 64, result={}, snapshot={}, budget_grant={}))
        db.commit()
    assert client.get("/desktop/update-safety", headers=auth(value, control=True)).json() == {"safe_to_restart": safe}
    with Session(app.state.engine) as db:
        assert db.get(VideoProjectJob, "update-guard-job").status == status


def test_configuration_is_pure_and_does_not_inherit_ambient_provider_settings(envelope, monkeypatch):
    from app import config
    monkeypatch.setenv("TASK_PROMPT_API_KEY", "must-not-inherit")
    monkeypatch.setenv("TASK_PROMPT_MODEL", "ambient-model")
    monkeypatch.setenv("TASK_VIDEO_SCENE_PROVIDER", "runninghub")
    before = os.environ.copy()
    getter = config.get_settings
    value = settings_for(envelope)
    assert config.get_settings is getter and os.environ == before
    assert value.settings.prompt_api_key == "" and value.settings.prompt_model == ""
    assert value.settings.database_url.endswith("/account-video/video.db")
    assert value.environment["TASK_VIDEO_COST_REVIEW_REQUIRED"] == "0"
    assert value.environment["TASK_VIDEO_SCENE_PROVIDER"] == "seedance"
    assert value.environment["TASK_VIDEO_SCENE_CREDENTIAL_SOURCE"] == "unconfigured"
    assert value.environment["TASK_VIDEO_RUNNINGHUB_KEY_TYPE"] == "SHARED"
    assert value.environment["TASK_VIDEO_PAID_ENABLED"] == "0"
    assert not value.data_dir.exists()


def test_existing_private_provider_configuration_is_reused_without_trial_database(envelope):
    directory = Path(envelope["config_dir"])
    directory.mkdir()
    (directory / "video-config.json").write_text(json.dumps({"TASK_VIDEO_PAID_ENABLED": "1", "RH_KEY_FILE": "offline-key-file"}), encoding="utf-8")
    (directory / "trial.db").write_bytes(b"must-not-open")
    called = []
    def loader(kind, result):
        def load(path):
            called.append((kind, path.name))
            return result
        return load
    value = configure(envelope, loaders={
        "primary": loader("primary", {"prompt_model": "offline-model", "prompt_api_key": "synthetic-model-key", "prompt_base_url": "https://example.test/models"}),
        "scene": loader("scene", {}), "voice": loader("voice", {"DASHSCOPE_API_KEY": "synthetic-voice-key"})})
    assert value.environment["TASK_VIDEO_PAID_ENABLED"] == "1"
    assert value.environment["RH_KEY_FILE"] == "offline-key-file"
    assert value.environment["DASHSCOPE_API_KEY"] == "synthetic-voice-key"
    assert value.settings.prompt_model == "offline-model"
    assert all(name.endswith(".dpapi") for _, name in called)
    assert (directory / "trial.db").read_bytes() == b"must-not-open"


@pytest.mark.parametrize("provider", ["seedance", "runninghub"])
def test_configured_scene_provider_and_hermes_scene_origin_are_explicit(envelope, provider):
    directory = Path(envelope["config_dir"])
    directory.mkdir()
    (directory / "video-config.json").write_text(json.dumps({"TASK_VIDEO_SCENE_PROVIDER": provider, "TASK_VIDEO_PAID_ENABLED": "1", "RUNNINGHUB_API_KEY": "synthetic-avatar-key"}))
    scene = {"seedance_api_key": "synthetic-ark-key", "seedance_base_url": "https://ark.cn-beijing.volces.com/api/v3", "seedance_model": "doubao-seedance-2-0-mini-260615"}
    value = configure(envelope, loaders={"primary": lambda _: {}, "scene": lambda _: scene, "voice": lambda _: {}})
    assert value.settings.seedance_api_key == "synthetic-ark-key"
    assert value.environment["TASK_VIDEO_SCENE_PROVIDER"] == provider
    assert value.environment["TASK_VIDEO_SCENE_CREDENTIAL_SOURCE"] == ("hermes" if provider == "seedance" else "runninghub_shared_key")
    assert value.environment["RUNNINGHUB_API_KEY"] == "synthetic-avatar-key"


def test_unknown_desktop_scene_provider_is_not_replaced_by_a_default(envelope):
    directory = Path(envelope["config_dir"]); directory.mkdir()
    (directory / "video-config.json").write_text(json.dumps({"TASK_VIDEO_SCENE_PROVIDER": "unknown"}))
    with pytest.raises(DesktopConfigurationError, match="no automatic fallback"):
        settings_for(envelope)


def test_identity_hash_includes_issuer_and_subject(envelope):
    value = settings_for(envelope)
    assert value.identity_hash == hashlib.sha256(b"https://example.test/task-api\x0047").hexdigest()
    second = copy.deepcopy(envelope)
    second["identity"]["backend_url"] = "https://other.test/task-api"
    assert settings_for(second).identity_hash != value.identity_hash
    second["identity"] = {**envelope["identity"], "id": 48}
    assert settings_for(second).identity_hash != value.identity_hash


@pytest.mark.parametrize("url", ["https://user:password@example.test", "https://example.test?key=secret", "http://public.test", "file:///tmp/x", "https://example.test/#part"])
def test_identity_issuer_rejects_unsafe_or_ambiguous_backends(url):
    with pytest.raises(DesktopConfigurationError):
        normalized_issuer(url)


@pytest.mark.parametrize("change", ["same_tokens", "short_token", "wildcard_origin", "id_boolean", "unknown_field", "same_directory"])
def test_invalid_private_bootstrap_is_rejected(envelope, change):
    if change == "same_tokens": envelope["control_token"] = envelope["capability_token"]
    if change == "short_token": envelope["capability_token"] = "short"
    if change == "wildcard_origin": envelope["cors_origins"] = ["*"]
    if change == "id_boolean": envelope["identity"]["id"] = True
    if change == "unknown_field": envelope["upstream_token"] = "must-not-be-accepted"
    if change == "same_directory": envelope["data_dir"] = envelope["config_dir"]
    with pytest.raises(DesktopConfigurationError): settings_for(envelope)


def test_status_and_video_routes_require_distinct_account_capability(desktop):
    value, app, client, _ = desktop
    for headers in ({}, {"Authorization": "Bearer wrong"}, auth(value, control=True)):
        assert client.get("/desktop/status", headers=headers).status_code == 401
        assert client.get("/api/v1/video/projects", headers=headers).status_code == 401
    response = client.get("/desktop/status", headers=auth(value))
    assert response.status_code == 200
    assert response.json()["identity_hash"] == value.identity_hash
    assert response.json()["cost_review_required"] is False
    assert response.json()["scene_provider"] == "seedance"
    assert response.json()["provider_ready"] == {}
    assert response.json()["capabilities_checked"] is False
    assert value.capability_token not in response.text and value.control_token not in response.text


def test_only_control_token_can_renew_expired_local_lease(desktop):
    value, _, client, now = desktop
    now[0] += 300
    assert client.get("/desktop/status", headers=auth(value)).status_code == 401
    assert client.post("/desktop/lease", headers=auth(value)).status_code == 401
    assert client.post("/desktop/lease", headers=auth(value, control=True)).json()["lease_seconds"] == 300
    assert client.get("/desktop/status", headers=auth(value)).status_code == 200


def test_real_identity_projection_has_no_login_sessions_or_original_business_routes(desktop):
    value, app, client, _ = desktop
    from app.models import UserAccount
    with Session(app.state.engine) as db:
        users = list(db.scalars(select(UserAccount)))
        assert len(users) == 1 and users[0].id == 47
        assert users[0].username == "verified-person" and users[0].role == "member"
        assert users[0].password_hash == "external-identity:no-local-login"
    tables = set(inspect(app.state.engine).get_table_names())
    assert tables == {"staff", "user_account", "video_project", "video_project_review", "video_project_job"}
    for path in ("/api/v1/auth/login", "/api/v1/auth/users", "/api/v1/tasks", "/api/v1/knowledge", "/api/v1/video/generations", "/docs", "/openapi.json"):
        assert client.post(path, headers=auth(value), json={}).status_code == 404


def test_metadata_prevents_account_or_backend_switch_in_existing_directory(desktop, envelope):
    _, _, _, _ = desktop
    envelope["identity"]["id"] = 48
    with pytest.raises(DesktopConfigurationError, match="different verified account"):
        create_app(settings_for(envelope))


def test_unknown_database_and_trial_data_are_never_adopted(envelope):
    folder = Path(envelope["data_dir"])
    folder.mkdir()
    (folder / "video.db").write_bytes(b"unknown database")
    with pytest.raises(DesktopConfigurationError, match="no verified owner"):
        create_app(settings_for(envelope))
    (folder / "trial.db").write_bytes(b"legacy")
    with pytest.raises(DesktopConfigurationError, match="trial data"):
        settings_for(envelope)


def test_reopening_same_real_identity_retains_video_data_without_trial_user(desktop):
    value, app, _, _ = desktop
    from app.video_project_models import VideoProject
    with Session(app.state.engine) as db:
        db.add(VideoProject(id="offline-retained-project", owner_id=47, topic="retained", mode="standard", platforms=[]))
        db.commit()
    reopened = create_app(value, capabilities=lambda: {})
    with Session(reopened.state.engine) as db:
        assert db.get(VideoProject, "offline-retained-project").owner_id == 47
    reopened.state.engine.dispose()


def test_real_runner_announces_only_private_ready_and_exits_on_parent_eof(envelope, tmp_path):
    """Launch the actual sidecar with an empty config; only localhost is used."""
    import httpx
    runner = Path(__file__).resolve().parents[1] / "scripts/run_video_desktop.py"
    process = subprocess.Popen([sys.executable, str(runner)], cwd=tmp_path,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        process.stdin.write(json.dumps(envelope) + "\n")
        process.stdin.flush()
        ready = json.loads(lines.get(timeout=15))
        assert set(ready) == {"event", "port", "instance_id"}
        assert ready["event"] == "ready" and ready["instance_id"] == envelope["instance_id"]
        with httpx.Client(base_url=f"http://127.0.0.1:{ready['port']}", trust_env=False, timeout=3) as client:
            assert client.get("/desktop/status").status_code == 401
            status = client.get("/desktop/status", headers={"Authorization": "Bearer " + envelope["capability_token"]})
            assert status.status_code == 200 and status.json()["cost_review_required"] is False
            assert status.json()["provider_ready"] == {}
            assert status.json()["capabilities_checked"] is False
            assert client.post("/api/v1/auth/login", json={}).status_code == 404
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        assert process.stdout.read() == ""
        assert not (Path(envelope["data_dir"]) / "trial.db").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()
