"""Offline production API tests. All provider calls are replaced by a local fake."""
import copy
import base64
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import zipfile

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TASK_DATABASE_URL", "sqlite://")

from app.auth import AuthPrincipal, require_api_key
from app.config import Settings
from app.database import Base, get_db
from app.models import Staff, UserAccount
from app import video_projects as api
from app.video_project_models import VideoProject, VideoProjectJob, VideoProjectReview


def plan(topic="太阳为什么发光"):
    return {
        "brief": {"title": topic, "audience": "对自然现象好奇的成年人", "core_message": "解释太阳发光的原理"},
        "audio_plan": {"narration_style": "短句解释", "voice": "中性旁白", "music": "轻音乐", "sound_notes": "旁白清晰"},
        "scenes": [{"id": "scene-1", "title": "提出问题", "duration": 6,
                    "narration": "太阳为什么能一直发光？", "visual_intent": "展示太阳", "seedance_prompt": "太阳表面，缓慢推进，科学示意。"}],
        "interest_hooks": [{"id": "hook-1", "scene_id": "scene-1", "kind": "opening", "copy": "太阳为什么能一直发光？", "payoff_scene_id": "scene-1"}],
    }


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.preparations = []
        self.amount = 2.5
        self.ready = True
        self.fail_submit = False
        self.scene_queued = False
        self.scene_provider = "runninghub"

    def runtime_capabilities(self):
        return {kind: {"ready": self.ready, "reason": "" if self.ready else "测试配置缺失"} for kind in ("voice", "scene", "scene_batch", "avatar", "render")}

    def estimate_job(self, job, project):
        return {"amount": 0 if job["kind"] == "render" else self.amount, "currency": "CNY", "basis": "测试中的固定费用上界，非真实报价"}

    def prepare_job(self, job, project):
        self.preparations.append(copy.deepcopy(job))
        if job["kind"] in {"scene", "scene_batch"}:
            ids = job.get("scene_ids") or [job.get("scene_id")]
            execution = {"schema": "offline-direct-scene/v1", "provider": self.scene_provider, "model": "offline-model",
                         "scenes": {key: {"route": "/offline-direct-route", "body": {"duration": "6"}} for key in ids}}
            return {"provider": self.scene_provider, "model": "offline-model", "execution": execution}
        return {"provider": {"voice": "oldx-bailian", "avatar": "oldx-runninghub", "render": "local-ffmpeg"}[job["kind"]]}

    def validate_job(self, job, project):
        if not self.ready:
            raise HTTPException(503, "测试配置缺失")

    async def execute_job(self, job, project, workdir, progress_callback):
        self.calls.append(copy.deepcopy(job))
        if self.fail_submit:
            await progress_callback({"status": "submitting"})
            raise RuntimeError("模拟连接断开且未取得提交回执")
        if self.scene_queued and job["kind"] == "scene" and not job.get("provider_task_id"):
            await progress_callback({"provider_task_id": "provider-existing-1", "status": "queued"})
            return {"status": "queued", "provider_task_id": "provider-existing-1", "result": {}}
        if job["kind"] == "scene_batch":
            children = []
            for scene_id in job["scene_ids"]:
                filename = scene_id + ".mp4"
                (workdir / filename).write_bytes(b"offline scene batch artifact")
                children.append({"scene_id": scene_id, "status": "succeeded", "path": filename})
            return {"status": "succeeded", "result": {"scene_ids": job["scene_ids"], "scene_results": children}}
        filename = "voice.wav" if job["kind"] == "voice" else "final.mp4" if job["kind"] == "render" else "scene.mp4"
        (workdir / filename).write_bytes(b"offline test artifact; not a decoded media assertion")
        key = "audio_path" if job["kind"] == "voice" else "path"
        return {"status": "succeeded", "provider_task_id": job.get("provider_task_id"), "result": {key: filename}}


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("TASK_VIDEO_COST_REVIEW_REQUIRED", "1")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        for uid in (1, 2):
            db.add(UserAccount(id=uid, username=f"member-{uid}", password_hash="unused-in-overridden-auth", staff=Staff(name=f"成员{uid}")))
        db.commit()
    runtime = FakeRuntime()
    settings = Settings(_env_file=None, prompt_model="test-planner", prompt_api_key="offline-test-only")
    settings.__dict__["video_storage_dir"] = str(tmp_path)
    monkeypatch.setattr(api, "runtime_module", lambda: runtime)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(api.router)

    def session():
        with factory() as db:
            yield db

    def principal(x_test_user: str = Header(default="1")):
        if x_test_user == "service":
            return AuthPrincipal(user=None, service=True)
        return AuthPrincipal(user=SimpleNamespace(id=int(x_test_user)))

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[require_api_key] = principal
    client = TestClient(app)
    yield client, runtime, factory, tmp_path
    client.close()
    engine.dispose()


def create(client, topic="太阳为什么发光", mode=None):
    payload = {"topic": topic}
    if mode:
        payload["mode"] = mode
    response = client.post("/api/v1/video/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def save(client, project, value=None, **extra):
    response = client.put(f"/api/v1/video/projects/{project['id']}/plan", json={"expected_revision": project["revision"], "plan": value or plan(), "locked_fields": [], **extra})
    assert response.status_code == 200, response.text
    return response.json()


def review(client, project, stage, decision="approved"):
    return client.post(f"/api/v1/video/projects/{project['id']}/reviews", json={"expected_revision": project["revision"], "stage": stage, "decision": decision, "quote": "确认这一步" if decision == "approved" else "明确跳过这一步"})


def prepared(client):
    project = save(client, create(client))
    assert review(client, project, "inputs").status_code == 200
    assert review(client, project, "voice", "skipped").status_code == 200
    assert review(client, project, "generation").status_code == 200
    return project


def job(client, project, kind="scene", key="same-click-stable-key", **extra):
    payload = {"expected_revision": project["revision"], "kind": kind, "idempotency_key": key,
               "budget_grant": {"max_cost": 3, "currency": "CNY", "quote": "确认本次任务最多支出三元"}}
    if kind == "scene":
        payload["scene_id"] = "scene-1"
    payload.update(extra)
    return client.post(f"/api/v1/video/projects/{project['id']}/jobs", json=payload)


def test_project_defaults_are_standard_persistent_and_personal(fixture):
    client, runtime, _, _ = fixture
    project = create(client)
    assert project["mode"] == "standard" and project["plan"] == {} and project["status"] == "draft"
    assert project["platforms"] == ["视频号", "抖音", "小红书"]
    assert client.get(f"/api/v1/video/projects/{project['id']}").json()["topic"] == project["topic"]
    assert client.get("/api/v1/video/projects", headers={"X-Test-User": "2"}).json() == {"items": []}
    for suffix in ("", "/quote?kind=voice", "/files/voice.wav", "/package"):
        assert client.get(f"/api/v1/video/projects/{project['id']}{suffix}", headers={"X-Test-User": "2"}).status_code == 404
    assert client.get("/api/v1/video/projects", headers={"X-Test-User": "service"}).status_code == 401
    assert runtime.calls == []


def test_budget_must_cover_reservation_not_only_point_estimate(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = prepared(client)
    monkeypatch.setattr(runtime, "estimate_job", lambda *_: {"amount": 2.5, "reservation_amount": 3.5,
        "currency": "CNY", "basis": "offline official-pricing fixture", "provider_hard_cap": False})
    response = job(client, project)
    assert response.status_code == 428 and runtime.calls == []
    response = job(client, project, budget_grant={"max_cost": 4, "currency": "CNY", "quote": "确认本批软件预留并理解供应商实际结算"})
    assert response.status_code == 202 and len(runtime.calls) == 1


def direct_mode(runtime, monkeypatch):
    monkeypatch.setenv("TASK_VIDEO_COST_REVIEW_REQUIRED", "0")
    def no_pricing(*args, **kwargs):
        pytest.fail("Direct generation must never call price-preview or estimate_job")
    monkeypatch.setattr(runtime, "estimate_job", no_pricing)


def direct_job(client, project, kind="scene", **extra):
    values = {"generation_consent": True, "budget_grant": None,
              **({"expected_provider": "runninghub"} if kind in {"scene", "scene_batch"} else {}), **extra}
    return job(client, project, kind, **values)


def test_direct_capabilities_and_legacy_quote_endpoint_never_fetch_price(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    capabilities = client.get("/api/v1/video/projects/capabilities").json()
    assert capabilities["cost_review_required"] is False
    fetched = client.get(f"/api/v1/video/projects/{project['id']}").json()
    assert fetched["capabilities"]["cost_review_required"] is False
    information = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    assert information["cost_review_required"] is False and information["amount"] is None
    assert information["basis"] == "不提供报价" and not information.get("blocked_reason")
    assert "quote_fingerprint" not in information


def test_direct_scene_uses_button_consent_and_pins_execution_without_budget(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    monkeypatch.setattr(api, "_quote", lambda *args, **kwargs: pytest.fail("No quote path in direct submission"))
    response = direct_job(client, project)
    assert response.status_code == 202, response.text
    assert len(runtime.calls) == 1 and len(runtime.preparations) == 1
    actual = runtime.calls[0]
    assert actual["budget_grant"]["generation_consent"] is True
    assert actual["budget_grant"]["intent"] == "用户点击生成，云端按供应商规则计费"
    assert "max_cost" not in actual["budget_grant"] and "currency" not in actual["budget_grant"]
    assert actual["execution"]["provider"] == "runninghub"
    assert actual["estimated_cost"]["amount"] is None and actual["estimated_cost"]["basis"] == "不提供报价"
    with factory() as db:
        saved = db.get(VideoProjectJob, response.json()["id"])
        assert saved.snapshot["_scene_execution"] == actual["execution"]
        assert saved.budget_grant["scope"] == "scene" and saved.budget_grant["scene_id"] == "scene-1"


@pytest.mark.parametrize("kind", ["scene", "scene_batch"])
def test_direct_seedance_provider_is_confirmed_and_pinned_without_pricing(fixture, monkeypatch, kind):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    runtime.scene_provider = "seedance"
    scope = {"scene_ids": ["scene-1"]} if kind == "scene_batch" else {}
    assert direct_job(client, project, kind, **scope).status_code == 428
    response = direct_job(client, project, kind, expected_provider="seedance", **scope)
    assert response.status_code == 202, response.text
    with factory() as db:
        saved = db.get(VideoProjectJob, response.json()["id"])
        assert saved.snapshot["_scene_execution"]["provider"] == "seedance"
        assert saved.budget_grant["expected_provider"] == "seedance"
    assert runtime.calls[-1]["execution"]["provider"] == "seedance"
    assert runtime.calls[-1]["estimated_cost"]["amount"] is None


def test_runninghub_receipt_refresh_keeps_pinned_route_after_seedance_switch(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    runtime.scene_queued = True
    first = direct_job(client, project)
    assert first.status_code == 202
    runtime.scene_provider = "seedance"
    response = client.post(f"/api/v1/video/projects/{project['id']}/jobs/{first.json()['id']}/refresh")
    assert response.status_code == 200, response.text
    assert len(runtime.preparations) == 1
    assert len(runtime.calls) == 2 and runtime.calls[-1]["execution"]["provider"] == "runninghub"
    assert runtime.calls[-1]["provider_task_id"] == "provider-existing-1"


@pytest.mark.parametrize("kind", ["voice", "scene", "scene_batch", "avatar"])
def test_direct_paid_work_requires_explicit_generation_consent(fixture, monkeypatch, kind):
    client, runtime, _, _ = fixture
    if kind == "avatar":
        project = avatar_prepared(client, trim=False)
    else:
        project = prepared(client)
    direct_mode(runtime, monkeypatch)
    response = direct_job(client, project, kind, generation_consent=False,
        **({"scene_ids": ["scene-1"]} if kind == "scene_batch" else {}))
    assert response.status_code == 428 and runtime.preparations == [] and runtime.calls == []


@pytest.mark.parametrize("provider", [None, "seedance"])
def test_direct_scene_requires_the_intended_runninghub_provider(fixture, monkeypatch, provider):
    client, runtime, _, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    response = direct_job(client, project, expected_provider=provider)
    assert response.status_code == 428 and runtime.calls == []


@pytest.mark.parametrize("mutated", ["provider", "execution"])
def test_direct_prepared_provider_drift_never_silently_uses_ark(fixture, monkeypatch, mutated):
    client, runtime, _, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    original = runtime.prepare_job
    def prepare(request, current):
        prepared = original(request, current)
        if mutated == "provider":
            prepared["provider"] = "seedance"
        else:
            prepared["execution"]["provider"] = "seedance"
        return prepared
    monkeypatch.setattr(runtime, "prepare_job", prepare)
    response = direct_job(client, project)
    assert response.status_code == 428 and runtime.calls == []


def test_direct_batch_preserves_list_and_requires_no_price_confirmation(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    value = plan()
    value["scenes"].append({**value["scenes"][0], "id": "scene-2"})
    project = save(client, create(client), value)
    for stage, decision in (("inputs", "approved"), ("voice", "skipped"), ("generation", "approved")):
        assert review(client, project, stage, decision).status_code == 200
    direct_mode(runtime, monkeypatch)
    response = direct_job(client, project, "scene_batch", scene_ids=["scene-1", "scene-2"])
    assert response.status_code == 202, response.text
    with factory() as db:
        saved = db.get(VideoProjectJob, response.json()["id"])
        assert saved.budget_grant["scene_ids"] == ["scene-1", "scene-2"]
        assert saved.snapshot["_scene_batch_ids"] == ["scene-1", "scene-2"]
        assert set(saved.snapshot["_scene_execution"]["scenes"]) == {"scene-1", "scene-2"}


def test_direct_free_render_does_not_require_paid_consent(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    response = direct_job(client, project, "render", generation_consent=False)
    assert response.status_code == 202 and len(runtime.calls) == 1
    assert runtime.calls[0]["budget_grant"]["generation_consent"] is False
    assert "不调用付费供应商" in runtime.calls[0]["budget_grant"]["intent"]


def test_direct_idempotency_binds_consent_and_provider_across_mode_changes(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    first = direct_job(client, project)
    assert first.status_code == 202
    assert direct_job(client, project).json()["id"] == first.json()["id"]
    assert direct_job(client, project, expected_provider="seedance").status_code == 409
    assert direct_job(client, project, generation_consent=False).status_code == 409
    monkeypatch.setenv("TASK_VIDEO_COST_REVIEW_REQUIRED", "1")
    replay = direct_job(client, project)
    assert replay.status_code == 202 and replay.json()["id"] == first.json()["id"]
    assert len(runtime.calls) == 1 and len(runtime.preparations) == 1


def test_legacy_idempotency_stays_valid_when_direct_mode_is_enabled(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = prepared(client)
    old = job(client, project)
    assert old.status_code == 202
    direct_mode(runtime, monkeypatch)
    replay = job(client, project)
    assert replay.status_code == 202 and replay.json()["id"] == old.json()["id"]
    assert direct_job(client, project).status_code == 409
    assert runtime.preparations == [] and len(runtime.calls) == 1


def test_direct_prepare_rechecks_revision_before_persisting(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    direct_mode(runtime, monkeypatch)
    original = runtime.prepare_job
    def prepare(request, current):
        prepared = original(request, current)
        with factory() as concurrent:
            concurrent.execute(update(VideoProject).where(VideoProject.id == project["id"]).values(revision=project["revision"] + 1))
            concurrent.commit()
        return prepared
    monkeypatch.setattr(runtime, "prepare_job", prepare)
    response = direct_job(client, project)
    assert response.status_code == 409 and runtime.calls == []
    with factory() as db:
        assert db.scalars(select(VideoProjectJob)).all() == []


def test_direct_mode_keeps_review_and_scope_requirements(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = save(client, create(client))
    direct_mode(runtime, monkeypatch)
    assert direct_job(client, project).status_code == 428
    project = prepared(client)
    assert direct_job(client, project, "voice", scene_id="scene-1").status_code == 422
    assert direct_job(client, project, scene_ids=["scene-1"]).status_code == 422
    assert runtime.calls == [] and runtime.preparations == []


def runninghub_quote(runtime, monkeypatch, *, overrides=None):
    """Deterministic price-preview double, including its pinned execution body."""
    state = overrides if overrides is not None else {}
    calls = []
    def estimate(request, project):
        calls.append(copy.deepcopy(request))
        ids = request.get("scene_ids") or [request.get("scene_id")]
        specification = {"provider": "runninghub", "model": "offline-seedance-model",
                         "route": "/offline/runninghub/seedance", "scene_ids": ids,
                         "duration": 6, "resolution": "720p", "ratio": "9:16", "audio": False}
        fingerprint = hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()
        value = {"amount": 2.5, "reservation_amount": 3, "currency": "CNY",
                 "basis": "Offline RunningHub price-preview fixture", "provider": "runninghub",
                 "model": specification["model"], "route": specification["route"],
                 "request_spec": specification, "request_fingerprint": fingerprint,
                 "execution": {"schema": "offline-execution/v1", **specification},
                 "preview_request_id": "volatile-preview-request", "fetched_at": "volatile-fetch-time"}
        value.update(copy.deepcopy(state))
        return value
    monkeypatch.setattr(runtime, "estimate_job", estimate)
    return state, calls


def test_runninghub_quote_binds_stable_facts_not_preview_metadata(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = prepared(client)
    state, _ = runninghub_quote(runtime, monkeypatch)
    url = f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1"
    first = client.get(url).json()
    assert len(first["quote_fingerprint"]) == 64
    state.update(preview_request_id="new-preview-request", fetched_at="new-fetch-time", basis="Different explanatory wording")
    second = client.get(url).json()
    assert second["quote_fingerprint"] == first["quote_fingerprint"]
    binding = {key: first.get(key) for key in ("project_id", "revision", "kind", "scene_id", "scene_ids",
        "provider", "model", "route", "request_fingerprint", "amount", "reservation_amount", "currency")}
    binding["project_id"] = project["id"]
    expected = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert first["quote_fingerprint"] == expected


@pytest.mark.parametrize("fingerprint", [None, "0" * 64])
def test_runninghub_new_jobs_require_the_viewed_quote_fingerprint(fixture, monkeypatch, fingerprint):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch)
    response = job(client, project, quote_fingerprint=fingerprint)
    assert response.status_code == 428 and "重新" in response.json()["detail"]["message"]
    assert runtime.calls == []
    with factory() as db:
        assert db.scalars(select(VideoProjectJob)).all() == []


@pytest.mark.parametrize("change", [
    {"provider": "seedance"}, {"model": "changed-model"}, {"route": "/different-model-route"},
    {"request_fingerprint": "1" * 64}, {"amount": 2.6}, {"reservation_amount": 3.1}, {"currency": "RH"},
])
def test_runninghub_quote_drift_requires_a_new_confirmation(fixture, monkeypatch, change):
    client, runtime, _, _ = fixture
    project = prepared(client)
    state, _ = runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    state.update(change)
    response = job(client, project, quote_fingerprint=viewed["quote_fingerprint"],
        budget_grant={"max_cost": 20, "currency": change.get("currency", "CNY"), "quote": "Confirm this viewed request"})
    assert response.status_code == 428 and runtime.calls == []


def test_runninghub_accepted_execution_is_pinned_and_idempotent_before_repricing(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    state, preview_calls = runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    created = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    assert created.status_code == 202, created.text
    assert runtime.calls[0]["execution"] == viewed["execution"]
    assert runtime.calls[0]["estimated_cost"]["quote_fingerprint"] == viewed["quote_fingerprint"]
    with factory() as db:
        saved = db.get(VideoProjectJob, created.json()["id"])
        assert saved.snapshot["_scene_execution"] == viewed["execution"]
        assert saved.estimated_cost["request_fingerprint"] == viewed["request_fingerprint"]
        assert saved.budget_grant["estimated_cost"]["quote_fingerprint"] == viewed["quote_fingerprint"]
    previous_count = len(preview_calls)
    state.update(route="/new-default-route", amount=50)
    repeated = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    assert repeated.status_code == 202 and repeated.json()["id"] == created.json()["id"]
    assert len(preview_calls) == previous_count and len(runtime.calls) == 1
    changed = job(client, project, quote_fingerprint="1" * 64)
    assert changed.status_code == 409 and len(runtime.calls) == 1


def test_runninghub_batch_quote_binds_fixed_list_and_snapshots_execution(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    value = plan()
    value["scenes"].append({**value["scenes"][0], "id": "scene-2"})
    project = save(client, create(client), value)
    for stage, decision in (("inputs", "approved"), ("voice", "skipped"), ("generation", "approved")):
        assert review(client, project, stage, decision).status_code == 200
    runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene_batch").json()
    assert job(client, project, "scene_batch", scene_ids=viewed["scene_ids"]).status_code == 428
    assert job(client, project, "scene_batch", scene_ids=list(reversed(viewed["scene_ids"])),
        quote_fingerprint=viewed["quote_fingerprint"]).status_code == 428
    accepted = job(client, project, "scene_batch", scene_ids=viewed["scene_ids"], quote_fingerprint=viewed["quote_fingerprint"])
    assert accepted.status_code == 202, accepted.text
    with factory() as db:
        saved = db.get(VideoProjectJob, accepted.json()["id"])
        assert saved.snapshot["_scene_batch_ids"] == viewed["scene_ids"]
        assert saved.snapshot["_scene_execution"] == viewed["execution"]
    assert runtime.calls[0]["execution"] == viewed["execution"]


def test_runninghub_resume_uses_pinned_route_after_default_switch(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runtime.scene_queued = True
    state, preview_calls = runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    created = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    assert created.status_code == 202 and created.json()["status"] == "queued"
    with factory() as db:
        saved = db.get(VideoProjectJob, created.json()["id"])
        saved.lease_token = "old-process:receipt"
        db.commit()
    state.update(provider="seedance", route="/new-default-ark")
    count = len(preview_calls)
    refreshed = client.post(f"/api/v1/video/projects/{project['id']}/jobs/{created.json()['id']}/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert len(preview_calls) == count
    assert runtime.calls[-1]["provider_task_id"] == "provider-existing-1"
    assert runtime.calls[-1]["execution"] == viewed["execution"]


def test_runninghub_missing_execution_never_creates_unpinned_work(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch, overrides={"execution": None})
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    response = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    assert response.status_code == 503 and runtime.calls == []


def test_runninghub_usd_quote_requires_and_preserves_original_currency(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch, overrides={"currency": "USD", "amount": .28, "reservation_amount": .336})
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    assert viewed["currency"] == "USD" and viewed["amount"] == .28 and viewed["reservation_amount"] == .336
    wrong_currency = job(client, project, quote_fingerprint=viewed["quote_fingerprint"],
        budget_grant={"max_cost": 10, "currency": "CNY", "quote": "CNY is not the viewed USD budget"})
    assert wrong_currency.status_code == 428 and runtime.calls == []
    accepted = job(client, project, quote_fingerprint=viewed["quote_fingerprint"],
        budget_grant={"max_cost": .34, "currency": "USD", "quote": "Confirm only this USD amount"})
    assert accepted.status_code == 202, accepted.text
    with factory() as db:
        saved = db.get(VideoProjectJob, accepted.json()["id"])
        assert saved.estimated_cost["currency"] == "USD" and saved.estimated_cost["amount"] == .28
        assert saved.budget_grant["currency"] == "USD" and saved.budget_grant["max_cost"] == .34
    assert runtime.calls[0]["budget_grant"]["currency"] == "USD"


@pytest.mark.parametrize("change", [{"provider": "seedance"}, {"route": "/different-channel-route"}, {"currency": "CNY"}])
def test_runninghub_usd_confirmation_never_silently_changes_channel_or_currency(fixture, monkeypatch, change):
    client, runtime, _, _ = fixture
    project = prepared(client)
    state, _ = runninghub_quote(runtime, monkeypatch, overrides={"currency": "USD"})
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    state.update(change)
    response = job(client, project, quote_fingerprint=viewed["quote_fingerprint"],
        budget_grant={"max_cost": 20, "currency": change.get("currency", "USD"), "quote": "Confirm the previously viewed original currency"})
    assert response.status_code == 428 and runtime.calls == []


def test_scene_capability_constraints_reach_planning_without_losing_metadata(fixture, monkeypatch):
    client, runtime, _, _ = fixture
    original = runtime.runtime_capabilities
    constraints = {"provider": "runninghub", "model": "offline-model", "route": "/offline-route",
                   "constraints": {"min_duration": 5, "max_duration": 15, "resolutions": ["720p"]}}
    def capabilities():
        caps = original()
        caps["scene"].update(copy.deepcopy(constraints))
        return caps
    monkeypatch.setattr(runtime, "runtime_capabilities", capabilities)
    project = create(client)
    assert project["capabilities"]["scene"]["constraints"] == constraints["constraints"]
    captured = {}
    async def compose(payload):
        captured.update(payload.model_dump())
        return {"value": plan()}
    monkeypatch.setattr(api, "compose_prompt", compose)
    response = client.post(f"/api/v1/video/projects/{project['id']}/compose",
        json={"expected_revision": project["revision"], "instruction": "Use the active video route constraints"})
    assert response.status_code == 200, response.text
    assert captured["context"]["scene_constraints"] == constraints


def test_price_preview_and_runtime_validation_precede_all_write_locks(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    trace = []
    engine = factory.kw["bind"]
    def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split()[0].upper() in {"UPDATE", "INSERT", "DELETE"}:
            trace.append("write")
    def capture_lock(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            trace.append("lock")
    original = runtime.estimate_job
    def preview(request, current):
        assert not {"write", "lock"}.intersection(trace)
        trace.append("preview")
        return original(request, current)
    def validate(request, snapshot):
        assert not {"write", "lock"}.intersection(trace)
        assert request["execution"] == viewed["execution"] == snapshot["_scene_execution"]
        trace.append("validate")
    monkeypatch.setattr(runtime, "estimate_job", preview)
    monkeypatch.setattr(runtime, "validate_job", validate)
    event.listen(engine, "before_cursor_execute", capture_sql)
    event.listen(Session, "do_orm_execute", capture_lock)
    try:
        response = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
        event.remove(Session, "do_orm_execute", capture_lock)
    assert response.status_code == 202, response.text
    assert "preview" in trace and "validate" in trace and "write" in trace and "lock" in trace
    assert max(trace.index("preview"), trace.index("validate")) < min(trace.index("write"), trace.index("lock"))


def test_revision_changed_during_quote_is_reloaded_before_claim(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    original = runtime.estimate_job
    def preview(request, current):
        quote = original(request, current)
        with factory() as concurrent:
            concurrent.execute(update(VideoProject).where(VideoProject.id == project["id"]).values(revision=project["revision"] + 1))
            concurrent.commit()
        return quote
    monkeypatch.setattr(runtime, "estimate_job", preview)
    response = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    assert response.status_code in (409, 428), response.text
    assert runtime.calls == []
    with factory() as db:
        assert db.get(VideoProject, project["id"]).revision == project["revision"] + 1
        assert db.scalars(select(VideoProjectJob)).all() == []


def test_review_revoked_during_quote_cannot_authorize_generation(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    original = runtime.estimate_job
    def preview(request, current):
        quote = original(request, current)
        with factory() as concurrent:
            concurrent.execute(update(VideoProjectReview).where(VideoProjectReview.project_id == project["id"],
                VideoProjectReview.revision == project["revision"], VideoProjectReview.stage == "generation").values(decision="invalidated"))
            concurrent.commit()
        return quote
    monkeypatch.setattr(runtime, "estimate_job", preview)
    response = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    assert response.status_code == 428 and runtime.calls == []
    with factory() as db:
        assert db.scalars(select(VideoProjectJob)).all() == []


def test_competing_idempotent_job_committed_during_quote_is_reused(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    request = {"kind": "scene", "scene_id": "scene-1", "scene_ids": None,
               "revision": project["revision"], "quote_fingerprint": viewed["quote_fingerprint"]}
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    original = runtime.estimate_job
    def preview(request, current):
        quote = original(request, current)
        with factory() as concurrent:
            concurrent.add(VideoProjectJob(id="concurrent-existing-job", project_id=project["id"], revision=project["revision"],
                kind="scene", scene_id="scene-1", idempotency_key="same-click-stable-key", request_hash=fingerprint,
                status="succeeded", result={"path": "already-created.mp4"}, snapshot={}, estimated_cost={}, budget_grant={}))
            concurrent.commit()
        return quote
    monkeypatch.setattr(runtime, "estimate_job", preview)
    response = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    assert response.status_code == 202 and response.json()["id"] == "concurrent-existing-job"
    assert runtime.calls == []
    with factory() as db:
        assert len(db.scalars(select(VideoProjectJob)).all()) == 1


def test_scene_completed_during_batch_quote_is_not_submitted_again(fixture, monkeypatch):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene_batch").json()
    original = runtime.estimate_job
    def preview(request, current):
        quote = original(request, current)
        with factory() as concurrent:
            concurrent.add(VideoProjectJob(id="completed-while-quoting", project_id=project["id"], revision=project["revision"],
                kind="scene", scene_id="scene-1", idempotency_key="concurrent-completion-key", request_hash="f" * 64,
                status="succeeded", result={"path": "completed.mp4"}, snapshot={}, estimated_cost={}, budget_grant={}))
            concurrent.commit()
        return quote
    monkeypatch.setattr(runtime, "estimate_job", preview)
    response = job(client, project, "scene_batch", scene_ids=viewed["scene_ids"], quote_fingerprint=viewed["quote_fingerprint"])
    assert response.status_code == 409 and runtime.calls == []
    with factory() as db:
        assert [row.id for row in db.scalars(select(VideoProjectJob)).all()] == ["completed-while-quoting"]


@pytest.mark.parametrize("change, expected_status", [("active_job", 409), ("owner_changed", 404)])
def test_eligibility_changed_during_quote_is_checked_under_claim(fixture, monkeypatch, change, expected_status):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runninghub_quote(runtime, monkeypatch)
    viewed = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    original = runtime.estimate_job
    def preview(request, current):
        quote = original(request, current)
        with factory() as concurrent:
            if change == "active_job":
                concurrent.add(VideoProjectJob(id="competing-active-job", project_id=project["id"], revision=project["revision"],
                    kind="voice", idempotency_key="different-concurrent-key", request_hash="f" * 64,
                    status="queued", result={}, snapshot={}, estimated_cost={}, budget_grant={}))
            else:
                concurrent.execute(update(VideoProject).where(VideoProject.id == project["id"]).values(owner_id=2))
            concurrent.commit()
        return quote
    monkeypatch.setattr(runtime, "estimate_job", preview)
    response = job(client, project, quote_fingerprint=viewed["quote_fingerprint"])
    assert response.status_code == expected_status and runtime.calls == []
    with factory() as db:
        assert len(db.scalars(select(VideoProjectJob)).all()) == int(change == "active_job")


def test_compose_saves_real_model_plan_and_respects_locked_fields(fixture, monkeypatch):
    client, _, _, _ = fixture
    project = save(client, create(client), locked_fields=["brief.title"])
    captured = {}

    async def compose(payload):
        captured.update(payload.model_dump())
        output = plan("AI试图修改锁定标题")
        output["scenes"][0]["narration"] = "新的分镜内容"
        return {"value": output}

    monkeypatch.setattr(api, "compose_prompt", compose)
    response = client.post(f"/api/v1/video/projects/{project['id']}/compose", json={"expected_revision": project["revision"], "instruction": "更易懂"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["plan"]["brief"]["title"] == project["plan"]["brief"]["title"]
    assert result["plan"]["scenes"][0]["narration"] == "新的分镜内容"
    assert captured["context"]["mode"] == "standard"
    assert captured["locked_fields"] == ["brief.title"]


def test_incomplete_plan_and_broken_interest_payoff_are_rejected(fixture):
    client, _, _, _ = fixture
    project = create(client)
    for value in ({"brief": {}}, {**plan(), "interest_hooks": [{"scene_id": "scene-1", "copy": "只吊胃口", "payoff_scene_id": "missing"}]}):
        response = client.put(f"/api/v1/video/projects/{project['id']}/plan", json={"expected_revision": 1, "plan": value})
        assert response.status_code == 422
    assert client.get(f"/api/v1/video/projects/{project['id']}").json()["revision"] == 1


def test_interest_hook_cannot_claim_an_answer_before_its_question(fixture):
    client, _, _, _ = fixture
    project = create(client)
    value = plan()
    value["scenes"].append({**value["scenes"][0], "id": "scene-2", "title": "第二幕"})
    value["interest_hooks"][0].update(scene_id="scene-2", payoff_scene_id="scene-1")
    response = client.put(f"/api/v1/video/projects/{project['id']}/plan", json={"expected_revision": 1, "plan": value})
    assert response.status_code == 422


def test_reviews_invalidate_on_content_revision_and_stale_write_fails(fixture):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    changed = plan()
    changed["scenes"][0]["narration"] = "不同的旁白内容"
    updated = save(client, project, changed)
    assert updated["revision"] == project["revision"] + 1 and updated["reviews"] == {}
    stale = client.put(f"/api/v1/video/projects/{project['id']}/plan", json={"expected_revision": project["revision"], "plan": changed})
    assert stale.status_code == 409
    assert review(client, project, "inputs").status_code == 409
    assert job(client, updated).status_code == 428
    assert runtime.calls == []
    with factory() as db:
        assert len(db.scalars(select(VideoProjectReview)).all()) == 3  # history retained


def test_voice_cost_requires_inputs_confirmation_and_own_budget(fixture):
    client, runtime, _, _ = fixture
    project = save(client, create(client))
    assert job(client, project, "voice").status_code == 428
    assert review(client, project, "inputs").status_code == 200
    assert job(client, project, "voice", budget_grant=None).status_code == 428
    response = job(client, project, "voice")
    assert response.status_code == 202, response.text
    result = client.get(f"/api/v1/video/projects/{project['id']}/jobs/{response.json()['id']}").json()
    assert result["status"] == "succeeded" and result["result"]["audio_path"] == "voice.wav"
    assert len(runtime.calls) == 1
    assert review(client, project, "voice").status_code == 200


def test_unproduced_voice_or_video_cannot_be_falsely_approved(fixture):
    client, _, _, _ = fixture
    project = save(client, create(client))
    assert review(client, project, "voice").status_code == 428
    assert review(client, project, "video").status_code == 428
    assert review(client, project, "generation").status_code == 428
    assert review(client, project, "voice", "skipped").status_code == 200
    assert job(client, project).status_code == 428  # skipping audition did not authorize content or spending


def test_unknown_quote_and_missing_configuration_never_submit(fixture):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runtime.amount = None
    quote = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=scene&scene_id=scene-1").json()
    assert quote["amount"] is None and quote["blocked_reason"]
    assert job(client, project).status_code == 503
    runtime.amount, runtime.ready = 2.5, False
    assert job(client, project).status_code == 503
    assert runtime.calls == []
    with factory() as db:
        assert db.scalars(select(VideoProjectJob)).all() == []


@pytest.mark.parametrize("grant", [None, {"max_cost": 2, "currency": "CNY", "quote": "最多两元"}, {"max_cost": 10, "currency": "RH", "quote": "十RH币"}, {"max_cost": 10, "currency": "CNY", "quote": "  "}])
def test_batch_budget_is_required_and_currency_bound(fixture, grant):
    client, runtime, _, _ = fixture
    project = prepared(client)
    assert job(client, project, budget_grant=grant).status_code == 428
    assert runtime.calls == []


def test_idempotent_same_key_only_submits_once_and_cannot_change_scope(fixture):
    client, runtime, _, _ = fixture
    project = prepared(client)
    first = job(client, project)
    second = job(client, project)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"] and len(runtime.calls) == 1
    assert job(client, project, "voice").status_code == 409
    current = client.get(f"/api/v1/video/projects/{project['id']}").json()
    assert len(current["jobs"]) == 1 and current["jobs"][0]["status"] == "succeeded"


def test_receipt_is_persisted_and_refresh_queries_same_task_after_restart(fixture):
    client, runtime, factory, _ = fixture
    project = prepared(client)
    runtime.scene_queued = True
    created = job(client, project).json()
    job_id = created["id"]
    with factory() as db:
        persisted = db.get(VideoProjectJob, job_id)
        assert persisted.provider_task_id == "provider-existing-1"
        persisted.lease_token = "previous-process:interrupted"
        persisted.status = "submitting"
        db.commit()
    response = client.post(f"/api/v1/video/projects/{project['id']}/jobs/{job_id}/refresh")
    assert response.status_code == 200, response.text
    result = client.get(f"/api/v1/video/projects/{project['id']}/jobs/{job_id}").json()
    assert result["status"] == "succeeded"
    assert len(runtime.calls) == 2 and runtime.calls[1]["provider_task_id"] == "provider-existing-1"


def test_missing_submit_receipt_never_automatically_repeats_payment(fixture):
    client, runtime, _, _ = fixture
    project = prepared(client)
    runtime.fail_submit = True
    created = job(client, project).json()
    fetched = client.get(f"/api/v1/video/projects/{project['id']}/jobs/{created['id']}").json()
    assert fetched["status"] == "needs_attention" and fetched["provider_task_id"] is None
    assert client.post(f"/api/v1/video/projects/{project['id']}/jobs/{created['id']}/refresh").status_code == 409
    assert job(client, project).json()["id"] == created["id"]
    assert len(runtime.calls) == 1


def medical_project(client, *, avatar=False):
    project = create(client, "体检发现尿酸升高", mode="avatar" if avatar else "standard")
    value = plan("体检发现尿酸升高")
    value["brief"]["core_message"] = "理解体检结果和常见原因"
    value["scenes"][0].update(narration="体检发现尿酸升高，先了解常见原因。", visual_intent="体检报告示意", seedance_prompt="体检报告，科学示意。")
    value["interest_hooks"][0]["copy"] = "尿酸升高一定是吃出来的吗？"
    # Caller-supplied facts stay as supplied; the API must not invent a reviewer.
    value["medical"] = {"is_medical": True, "reviewed": False, "reviewer": ""}
    if avatar:
        image = client.post(f"/api/v1/video/projects/{project['id']}/assets", json={"name": "demo.png", "data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3WQAAAAASUVORK5CYII="})
        assert image.status_code == 201, image.text
        value["avatar"] = {"image_path": image.json()["path"], "image_use_confirmed": True}
    return save(client, project, value)


@pytest.mark.parametrize("direct", [False, True], ids=["legacy-quote", "direct"])
@pytest.mark.parametrize("kind", ["voice", "scene", "avatar"])
def test_medical_generation_uses_normal_content_confirmations(fixture, monkeypatch, kind, direct):
    client, runtime, _, _ = fixture
    if direct:
        direct_mode(runtime, monkeypatch)
    project = medical_project(client, avatar=kind == "avatar")
    assert project["medical_gate"] == {"required": False, "policy": "user_content_confirmation"}
    assert project["plan"]["medical"] == {"is_medical": True, "reviewed": False, "reviewer": ""}
    submit = direct_job if direct else job
    query = f"/api/v1/video/projects/{project['id']}/quote?kind={kind}" + ("&scene_id=scene-1" if kind == "scene" else "")
    quoted = client.get(query)
    assert quoted.status_code == 200 and not quoted.json().get("blocked_reason")
    assert submit(client, project, kind).status_code == 428
    assert runtime.calls == []
    assert review(client, project, "inputs").status_code == 200
    if kind != "voice":
        assert submit(client, project, kind).status_code == 428
        assert review(client, project, "generation").status_code == 428
        audition = submit(client, project, "voice", key="medical-voice-audition")
        assert audition.status_code == 202, audition.text
        assert review(client, project, "voice").status_code == 200
        assert submit(client, project, kind).status_code == 428
        assert review(client, project, "generation").status_code == 200
    result = submit(client, project, kind)
    assert result.status_code == 202, result.text
    assert runtime.calls[-1]["kind"] == kind
    current = client.get(f"/api/v1/video/projects/{project['id']}").json()
    assert current["plan"]["medical"] == project["plan"]["medical"]
    assert current["medical_gate"] == {"required": False, "policy": "user_content_confirmation"}


@pytest.mark.parametrize("direct", [False, True], ids=["legacy-quote", "direct"])
def test_medical_export_requires_actual_final_content_confirmation(fixture, monkeypatch, direct):
    client, runtime, _, _ = fixture
    if direct:
        direct_mode(runtime, monkeypatch)
    project = medical_project(client)
    submit = direct_job if direct else job
    for stage, decision in (("inputs", "approved"), ("voice", "skipped"), ("generation", "approved")):
        assert review(client, project, stage, decision).status_code == 200
    assert client.get(f"/api/v1/video/projects/{project['id']}/package").status_code == 428
    assert review(client, project, "video").status_code == 428
    rendered = submit(client, project, "render", budget_grant=None)
    assert rendered.status_code == 202, rendered.text
    assert client.get(f"/api/v1/video/projects/{project['id']}/package").status_code == 428
    assert review(client, project, "video").status_code == 200
    package = client.get(f"/api/v1/video/projects/{project['id']}/package")
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        exported = json.loads(archive.read("production-plan.json"))
    assert exported["plan"]["medical"] == project["plan"]["medical"]
    assert exported["reviews"]["video"]["decision"] == "approved"


def test_avatar_requires_explicit_mode_and_shorter_segments(fixture):
    client, runtime, _, _ = fixture
    standard = prepared(client)
    assert job(client, standard, "avatar").status_code == 422
    avatar = create(client, mode="avatar")
    value = plan()
    value["scenes"][0]["duration"] = 15
    result = client.put(f"/api/v1/video/projects/{avatar['id']}/plan", json={"expected_revision": 1, "plan": value})
    assert result.status_code == 422 and runtime.calls == []


def test_owned_asset_upload_survives_revision_and_is_not_a_local_path_escape(fixture):
    client, runtime, _, root = fixture
    project = create(client, mode="avatar")
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3WQAAAAASUVORK5CYII=")
    request = {"name": "../../人物.png", "data_url": "data:image/png;base64," + base64.b64encode(png).decode(), "role": "avatar_image"}
    other = client.post(f"/api/v1/video/projects/{project['id']}/assets", json=request, headers={"X-Test-User": "2"})
    assert other.status_code == 404
    response = client.post(f"/api/v1/video/projects/{project['id']}/assets", json=request)
    assert response.status_code == 201, response.text
    uploaded = response.json()
    assert uploaded["path"].startswith("assets/") and ".." not in uploaded["path"]
    value = plan()
    value["avatar"] = {"image_path": uploaded["path"], "image_use_confirmed": True}
    project = save(client, project, value)
    assert client.get(f"/api/v1/video/projects/{project['id']}/files/{uploaded['path']}").content == png
    assert review(client, project, "inputs").status_code == 200
    assert job(client, project, "voice").status_code == 202
    assert (root / "1" / project["id"] / f"revision-{project['revision']}" / uploaded["path"]).read_bytes() == png
    invalid = {**request, "data_url": "data:image/svg+xml;base64,PHN2Zz4="}
    assert client.post(f"/api/v1/video/projects/{project['id']}/assets", json=invalid).status_code == 422


def test_final_preview_precedes_video_review_and_package_has_actual_files(fixture):
    client, _, _, _ = fixture
    project = prepared(client)
    created = job(client, project, "render", budget_grant=None)
    assert created.status_code == 202, created.text
    preview = client.get(f"/api/v1/video/projects/{project['id']}/files/final.mp4")
    assert preview.status_code == 200
    assert client.get(f"/api/v1/video/projects/{project['id']}/package").status_code == 428
    assert review(client, project, "video").status_code == 200
    package = client.get(f"/api/v1/video/projects/{project['id']}/package")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert set(archive.namelist()) == {"production-plan.json", "final.mp4"}
    assert client.get(f"/api/v1/video/projects/{project['id']}/files/%2e%2e/%2e%2e/.env").status_code == 404


def test_replacing_voice_invalidates_its_review_and_downstream_confirmations(fixture):
    client, _, factory, _ = fixture
    project = prepared(client)
    assert job(client, project, "render", key="render-original-version", budget_grant=None).status_code == 202
    assert review(client, project, "video").status_code == 200
    assert job(client, project, "voice", key="replace-current-voice").status_code == 202
    current = client.get(f"/api/v1/video/projects/{project['id']}").json()
    assert set(current["reviews"]) == {"inputs"}
    assert current["revision"] == project["revision"]
    assert job(client, project, key="new-scene-after-revoice").status_code == 428
    assert client.get(f"/api/v1/video/projects/{project['id']}/package").status_code == 428
    with factory() as db:
        assert db.scalar(select(VideoProjectReview).where(VideoProjectReview.stage == "video")).decision == "invalidated"


def test_new_render_cannot_reuse_previous_video_approval(fixture):
    client, _, _, _ = fixture
    project = prepared(client)
    assert job(client, project, "render", key="render-before-review", budget_grant=None).status_code == 202
    assert review(client, project, "video").status_code == 200
    assert client.get(f"/api/v1/video/projects/{project['id']}/package").status_code == 200
    assert job(client, project, "render", key="render-after-review", budget_grant=None).status_code == 202
    assert client.get(f"/api/v1/video/projects/{project['id']}/package").status_code == 428


def test_replaced_scene_requires_new_render_before_video_can_be_approved(fixture):
    client, _, _, _ = fixture
    project = prepared(client)
    assert job(client, project, "render", key="render-before-replaced-scene", budget_grant=None).status_code == 202
    assert job(client, project, "scene", key="replace-rendered-scene").status_code == 202
    assert review(client, project, "video").status_code == 428


def test_active_scene_cannot_be_double_submitted_or_share_voice_workspace(fixture):
    client, runtime, _, _ = fixture
    project = prepared(client)
    runtime.scene_queued = True
    assert job(client, project).status_code == 202
    assert job(client, project, key="different-key-same-scene").status_code == 409
    assert job(client, project, "voice", key="voice-during-scene").status_code == 409
    assert len(runtime.calls) == 1


def test_package_contains_platform_cover_subtitles_and_provenance(fixture):
    client, _, factory, root = fixture
    project = prepared(client)
    created = job(client, project, "render", budget_grant=None).json()
    directory = root / "1" / project["id"] / f"revision-{project['revision']}"
    for relative in ("platform/video.mp4", "platform/cover.jpg", "platform/subtitles.srt", "platform/manifest.json"):
        path = directory / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"offline package fixture")
    with factory() as db:
        row = db.get(VideoProjectJob, created["id"])
        row.result = {**row.result, "exports": [{"path": "platform/video.mp4", "cover_path": "platform/cover.jpg", "subtitle_path": "platform/subtitles.srt", "manifest_path": "platform/manifest.json"}]}
        db.commit()
    assert review(client, project, "video").status_code == 200
    response = client.get(f"/api/v1/video/projects/{project['id']}/package")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {"platform/video.mp4", "platform/cover.jpg", "platform/subtitles.srt", "platform/manifest.json"}.issubset(archive.namelist())


def test_avatar_disk_receipt_recovery_never_submits_a_replacement(fixture):
    client, runtime, factory, root = fixture
    from app import video_runtime
    runtime.recover_receipt = video_runtime.recover_receipt
    project = create(client, mode="avatar")
    image = client.post(f"/api/v1/video/projects/{project['id']}/assets", json={"name": "demo.png", "data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3WQAAAAASUVORK5CYII="}).json()
    value = plan()
    value["avatar"] = {"image_path": image["path"], "image_use_confirmed": True}
    project = save(client, project, value)
    for stage, decision in (("inputs", "approved"), ("voice", "skipped"), ("generation", "approved")):
        assert review(client, project, stage, decision).status_code == 200
    created = job(client, project, "avatar").json()
    with factory() as db:
        row = db.get(VideoProjectJob, created["id"])
        row.status, row.provider_task_id, row.lease_token = "blocked", None, "dead-process:lost-sql-receipt"
        db.commit()
    base = f"/api/v1/video/projects/{project['id']}/jobs/{created['id']}/refresh"
    assert client.post(base).status_code == 409
    avatar = root / "1" / project["id"] / f"revision-{project['revision']}" / "skill" / f"revision-{project['revision']}" / "avatar"
    avatar.mkdir(parents=True)
    (avatar / "submission_1.json").write_text(json.dumps({"segment": 1, "task": "existing-runninghub-task"}), encoding="utf-8")
    assert client.post(base).status_code == 200
    assert len(runtime.calls) == 2
    assert json.loads(runtime.calls[-1]["provider_task_id"]) == {"1": "existing-runninghub-task"}


def test_avatar_input_skip_does_not_bypass_portrait_use_confirmation(fixture):
    client, runtime, _, _ = fixture
    project = save(client, create(client, mode="avatar"))
    assert review(client, project, "inputs", "skipped").status_code == 428
    assert job(client, project, "voice").status_code == 428
    assert runtime.calls == []


def test_video_migration_adds_only_its_tables_and_matches_runtime_schema(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20261019_01_video_projects.py"
    spec = importlib.util.spec_from_file_location("video_projects_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        tables = migration.tables()
        tables[0].metadata.tables["user_account"].create(connection)
        monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: connection))
        migration.upgrade()
        migration.upgrade()  # local create_all deployment followed by migration is supported
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) == {"user_account", "video_project", "video_project_review", "video_project_job"}
        for table in tables:
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            assert actual == set(Base.metadata.tables[table.name].columns.keys())
        migration.downgrade()
        assert inspect(connection).get_table_names() == ["user_account"]
    engine.dispose()


def test_plan_normalizes_legacy_platforms_and_editable_direction_defaults(fixture):
    client, _, _, _ = fixture
    value = plan()
    value["persona"] = {"name": "星空讲解员", "description": "解释自然科学"}
    value["platform_versions"] = {"抖音": {"title": "旧标题", "caption": "旧简介"}}
    value["platform_variants"] = {"douyin": {"title": "确认标题"}}
    saved = save(client, create(client), value)
    canonical = saved["plan"]
    assert "platform_versions" not in canonical
    assert canonical["persona"] == "星空讲解员；解释自然科学"
    assert canonical["visual_style"] and canonical["layout"]
    assert canonical["platform_variants"]["douyin"]["title"] == "确认标题"
    assert canonical["platform_variants"]["douyin"]["caption"] == "旧简介"
    assert set(canonical["platform_variants"]) == {"weixin", "douyin", "xiaohongshu"}
    assert all(set(entry) == {"title", "cover_direction", "caption", "layout_notes"} and all(entry.values())
               for entry in canonical["platform_variants"].values())
    assert canonical["postproduction"] == {"trim_pauses": False, "burn_subtitles": True}
    assert client.get(f"/api/v1/video/projects/{saved['id']}").json()["plan"] == canonical


def test_avatar_planning_duration_and_postproduction_defaults_are_separate(fixture):
    client, _, _, _ = fixture
    value = plan()
    value["scenes"][0]["duration"] = 2
    avatar = save(client, create(client, mode="avatar"), value)
    assert avatar["plan"]["brief"]["total_duration"] == 2
    assert avatar["plan"]["postproduction"] == {"trim_pauses": False, "burn_subtitles": False}
    standard = create(client)
    response = client.put(f"/api/v1/video/projects/{standard['id']}/plan", json={"expected_revision": 1, "plan": value})
    assert response.status_code == 422


@pytest.mark.parametrize("options", [{"trim_pauses": "false"}, {"burn_subtitles": 1}, {"unknown": True}, []])
def test_postproduction_switches_require_boolean_values(fixture, options):
    client, _, _, _ = fixture
    project = create(client)
    value = {**plan(), "postproduction": options}
    response = client.put(f"/api/v1/video/projects/{project['id']}/plan", json={"expected_revision": 1, "plan": value})
    assert response.status_code == 422


def avatar_prepared(client, *, trim=True):
    project = create(client, mode="avatar")
    image = client.post(f"/api/v1/video/projects/{project['id']}/assets", json={"name": "demo.png", "data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3WQAAAAASUVORK5CYII="}).json()
    value = plan()
    value["avatar"] = {"image_path": image["path"], "image_use_confirmed": True}
    value["postproduction"] = {"trim_pauses": trim, "burn_subtitles": False}
    project = save(client, project, value)
    for stage, decision in (("inputs", "approved"), ("voice", "skipped"), ("generation", "approved")):
        assert review(client, project, stage, decision).status_code == 200
    return project


def postprocess_review(client, project, raw_id, decision="approved"):
    return client.post(f"/api/v1/video/projects/{project['id']}/reviews", json={
        "expected_revision": project["revision"], "stage": "postprocess", "decision": decision,
        "job_id": raw_id, "quote": "已播放当前数字人原片，确认去停顿"})


def test_postprocess_review_requires_actual_current_raw_avatar_and_cannot_skip(fixture):
    client, _, _, _ = fixture
    project = avatar_prepared(client)
    assert postprocess_review(client, project, "invented-raw").status_code == 428
    assert job(client, project, "render", budget_grant=None).status_code == 428
    raw = job(client, project, "avatar").json()
    assert postprocess_review(client, project, raw["id"], "skipped").status_code == 422
    assert postprocess_review(client, project, "another-project-job").status_code == 428
    confirmed = postprocess_review(client, project, raw["id"])
    assert confirmed.status_code == 200, confirmed.text
    record = confirmed.json()["reviews"]["postprocess"]
    assert record["job_id"] == raw["id"] and record["quote"] == "已播放当前数字人原片，确认去停顿"
    assert job(client, project, "render", key="trim-confirmed-local-render", budget_grant=None).status_code == 202
    replacement = job(client, project, "avatar", key="remake-current-avatar-raw").json()
    current = client.get(f"/api/v1/video/projects/{project['id']}").json()
    assert "postprocess" not in current["reviews"]
    assert postprocess_review(client, project, raw["id"]).status_code == 428
    assert postprocess_review(client, project, replacement["id"]).status_code == 200


def test_nontrimmed_avatar_does_not_require_pause_review(fixture):
    client, _, _, _ = fixture
    project = avatar_prepared(client, trim=False)
    assert job(client, project, "avatar").status_code == 202
    assert job(client, project, "render", key="nontrimmed-local-render", budget_grant=None).status_code == 202


def test_postproduction_only_change_reuses_local_media_without_new_provider_calls(fixture):
    client, runtime, factory, root = fixture
    project = avatar_prepared(client, trim=False)
    assert job(client, project, "voice").status_code == 202
    assert review(client, project, "voice").status_code == 200
    assert review(client, project, "generation").status_code == 200
    raw = job(client, project, "avatar", key="raw-before-option-change").json()
    skill_relative = f"skill/revision-{project['revision']}/avatar/final_avatar.mp4"
    skill_raw = root / "1" / project["id"] / f"revision-{project['revision']}" / skill_relative
    skill_raw.parent.mkdir(parents=True)
    skill_raw.write_bytes(b"offline original avatar footage")
    with factory() as db:
        row = db.get(VideoProjectJob, raw["id"])
        row.result = {**row.result, "path": skill_relative}
        db.commit()
    assert job(client, project, "render", key="render-before-options-change", budget_grant=None).status_code == 202
    assert review(client, project, "video").status_code == 200
    calls_before = len(runtime.calls)
    value = copy.deepcopy(project["plan"])
    value["postproduction"] = {"trim_pauses": True, "burn_subtitles": True}
    updated = save(client, project, value)
    assert updated["revision"] == project["revision"] + 1
    assert set(updated["reviews"]) == {"inputs", "voice", "generation"}
    assert all(record["reused_from_revision"] == project["revision"] for record in updated["reviews"].values())
    reused = [entry for entry in updated["jobs"] if entry["revision"] == updated["revision"]]
    assert {entry["kind"] for entry in reused} == {"voice", "avatar"}
    assert all(entry["estimated_cost"]["amount"] == 0 and not entry["budget_grant"] for entry in reused)
    new_raw = next(entry for entry in reused if entry["kind"] == "avatar")
    assert new_raw["result"]["reused_from_job_id"] == raw["id"]
    assert new_raw["result"]["path"] == f"skill/revision-{updated['revision']}/avatar/final_avatar.mp4"
    assert (root / "1" / project["id"] / f"revision-{updated['revision']}" / new_raw["result"]["path"]).read_bytes() == skill_raw.read_bytes()
    old_path = root / "1" / project["id"] / f"revision-{project['revision']}" / "scene.mp4"
    new_path = root / "1" / project["id"] / f"revision-{updated['revision']}" / "scene.mp4"
    assert old_path.read_bytes() == new_path.read_bytes()
    new_path.write_bytes(b"postprocessed copy")
    assert old_path.read_bytes() != new_path.read_bytes()  # never hard-link editable OLDX project files
    assert len(runtime.calls) == calls_before
    assert postprocess_review(client, updated, raw["id"]).status_code == 428
    assert postprocess_review(client, updated, new_raw["id"]).status_code == 200
    with factory() as db:
        assert db.get(VideoProjectJob, raw["id"]).revision == project["revision"]


def test_content_change_and_active_jobs_do_not_use_postproduction_reuse(fixture):
    client, runtime, _, _ = fixture
    project = prepared(client)
    runtime.scene_queued = True
    assert job(client, project).status_code == 202
    changed = copy.deepcopy(project["plan"])
    changed["postproduction"]["burn_subtitles"] = False
    response = client.put(f"/api/v1/video/projects/{project['id']}/plan", json={"expected_revision": project["revision"], "plan": changed})
    assert response.status_code == 409
    changed["scenes"][0]["narration"] = "旁白也变了，不能复用"
    updated = save(client, project, changed)
    assert updated["reviews"] == {}
    assert not any(entry["revision"] == updated["revision"] for entry in updated["jobs"])


def test_scene_batch_uses_fixed_missing_scene_ids_and_one_budget(fixture):
    client, runtime, _, _ = fixture
    value = plan()
    value["scenes"].append({**value["scenes"][0], "id": "scene-2"})
    project = save(client, create(client), value)
    for stage, decision in (("inputs", "approved"), ("voice", "skipped"), ("generation", "approved")):
        assert review(client, project, stage, decision).status_code == 200
    base = f"/api/v1/video/projects/{project['id']}"
    quote = client.get(base + "/quote?kind=scene_batch").json()
    assert quote["scope"] == "scene_batch" and quote["scene_ids"] == ["scene-1", "scene-2"]
    assert job(client, project, "scene_batch").status_code == 422
    assert job(client, project, "scene_batch", scene_ids=quote["scene_ids"], budget_grant=None).status_code == 428
    accepted = job(client, project, "scene_batch", scene_ids=quote["scene_ids"])
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["scene_ids"] == quote["scene_ids"]
    assert len(runtime.calls) == 1 and runtime.calls[0]["scene_ids"] == quote["scene_ids"]
    assert job(client, project, "scene_batch", scene_ids=quote["scene_ids"]).json()["id"] == accepted.json()["id"]
    assert job(client, project, "scene_batch", scene_ids=["scene-1"]).status_code == 409  # same idempotency key, changed scope
    assert client.get(base + "/quote?kind=scene_batch&scene_ids=scene-1").status_code == 409
    assert job(client, project, "scene_batch", key="new-batch-may-not-redo", scene_ids=["scene-1"]).status_code == 409
    assert job(client, project, "scene", key="explicit-authorized-redo").status_code == 202


def test_project_jobs_cannot_pretend_to_be_current_scene_and_preflight_errors_block(fixture):
    client, runtime, _, _ = fixture
    project = prepared(client)
    for kind in ("voice", "avatar", "render"):
        quote = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind={kind}&scene_id=scene-1")
        assert quote.status_code == 422
    def missing_preflight(job, project):
        raise ValueError("请先生成配音并核对实际分段")
    runtime.estimate_job = missing_preflight
    quote = client.get(f"/api/v1/video/projects/{project['id']}/quote?kind=voice").json()
    assert quote["amount"] is None and quote["scope"] == "project"
    assert "实际分段" in quote["blocked_reason"]
    assert job(client, project, "voice").status_code == 503
    assert runtime.calls == []


def test_publication_package_includes_canonical_platform_publication_text(fixture):
    client, runtime, _, _ = fixture
    value = plan()
    value["platform_versions"] = {"抖音": {"title": "旧平台标题", "caption": "旧平台简介"}}
    value["platform_variants"] = {"douyin": {"title": "确认后的太阳科普", "caption": "本片解释太阳发光的原理。",
        "cover_direction": "太阳示意与一个问题", "layout_notes": "字幕避开操作区"}}
    project = save(client, create(client), value)
    for stage, decision in (("inputs", "approved"), ("voice", "skipped"), ("generation", "approved")):
        assert review(client, project, stage, decision).status_code == 200
    original = runtime.execute_job
    async def renderer(job, snapshot, workdir, progress_callback):
        result = await original(job, snapshot, workdir, progress_callback)
        variant = snapshot["plan"]["platform_variants"]["douyin"]
        text = "\n".join(f"{key}: {variant[key]}" for key in ("title", "caption", "cover_direction", "layout_notes"))
        path = workdir / "douyin/publication.md"
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        result["result"]["exports"] = [{"publication_path": "douyin/publication.md"}]
        return result
    runtime.execute_job = renderer  # Only the renderer is simulated; the ZIP endpoint is real.
    assert job(client, project, "render", budget_grant=None).status_code == 202
    assert review(client, project, "video").status_code == 200
    response = client.get(f"/api/v1/video/projects/{project['id']}/package")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        text = archive.read("douyin/publication.md").decode("utf-8")
        assert "确认后的太阳科普" in text and "字幕避开操作区" in text
        assert "旧平台标题" not in text and "旧平台简介" not in text
        persisted = json.loads(archive.read("production-plan.json"))["plan"]
        assert persisted["platform_variants"]["douyin"] == project["plan"]["platform_variants"]["douyin"]


def test_publication_text_cannot_escape_the_project_directory(fixture):
    client, _, factory, root = fixture
    project = prepared(client)
    created = job(client, project, "render", budget_grant=None).json()
    outside = root / "publication.md"
    outside.write_text("unrelated document", encoding="utf-8")
    with factory() as db:
        row = db.get(VideoProjectJob, created["id"])
        row.result = {**row.result, "exports": [{"publication_path": "../../../publication.md"}]}
        db.commit()
    assert review(client, project, "video").status_code == 200
    assert client.get(f"/api/v1/video/projects/{project['id']}/package").status_code == 404
    assert outside.read_text(encoding="utf-8") == "unrelated document"


def test_interrupted_media_copy_cleans_only_staging_and_can_retry(fixture, monkeypatch):
    client, runtime, _, root = fixture
    project = prepared(client)
    assert job(client, project).status_code == 202
    directory = root / "1" / project["id"]
    source = directory / f"revision-{project['revision']}"
    original_bytes = (source / "scene.mp4").read_bytes()
    original_copy = api.shutil.copytree
    def interrupted_copy(source_path, destination_path, **kwargs):
        (Path(destination_path) / "partial.mp4").write_bytes(b"incomplete copy")
        raise OSError("offline simulated disk interruption")
    monkeypatch.setattr(api.shutil, "copytree", interrupted_copy)
    changed = copy.deepcopy(project["plan"])
    changed["postproduction"]["burn_subtitles"] = False
    response = client.put(f"/api/v1/video/projects/{project['id']}/plan", json={"expected_revision": project["revision"], "plan": changed})
    assert response.status_code == 503
    current = client.get(f"/api/v1/video/projects/{project['id']}").json()
    assert current["revision"] == project["revision"] and current["plan"] == project["plan"]
    assert (source / "scene.mp4").read_bytes() == original_bytes
    assert not (directory / f"revision-{project['revision'] + 1}").exists()
    assert list(directory.glob(".r????????")) == []
    monkeypatch.setattr(api.shutil, "copytree", original_copy)
    updated = save(client, project, changed)
    assert updated["revision"] == project["revision"] + 1
    assert (source / "scene.mp4").read_bytes() == original_bytes
    assert list(directory.glob(".r????????")) == []
    assert len(runtime.calls) == 1


def test_existing_revision_directory_is_never_deleted_or_overwritten(fixture):
    client, _, _, root = fixture
    project = prepared(client)
    assert job(client, project).status_code == 202
    destination = root / "1" / project["id"] / f"revision-{project['revision'] + 1}"
    destination.mkdir()
    marker = destination / "unrelated-existing.mp4"
    marker.write_bytes(b"preserve existing content")
    changed = copy.deepcopy(project["plan"])
    changed["postproduction"]["burn_subtitles"] = False
    response = client.put(f"/api/v1/video/projects/{project['id']}/plan", json={"expected_revision": project["revision"], "plan": changed})
    assert response.status_code == 409
    assert marker.read_bytes() == b"preserve existing content"
    assert client.get(f"/api/v1/video/projects/{project['id']}").json()["revision"] == project["revision"]


def test_staging_cleanup_refuses_a_path_outside_its_project(fixture):
    _, _, _, root = fixture
    project_root = root / "owned-project"
    project_root.mkdir()
    outside = root / ".reuse-outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("outside", encoding="utf-8")
    with pytest.raises(OSError):
        api._cleanup_reuse_staging(outside, project_root)
    assert marker.read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_long_hash_asset_path_reuses_without_longer_staging(fixture, monkeypatch):
    client, runtime, _, root = fixture
    relative = str(Path("1") / ("a" * 36) / "revision-2" / "assets" / ("f" * 64 + ".png"))
    # Match the 251-character path observed in the real local client E2E run.
    base = root / "media"
    padding = 251 - len(str(base)) - len(relative) - 2
    assert padding > 0
    storage = base / ("p" * padding)
    api.get_settings().__dict__["video_storage_dir"] = str(storage)
    project = avatar_prepared(client, trim=False)
    assert job(client, project, "avatar").status_code == 202
    source = storage / "1" / project["id"] / f"revision-{project['revision']}"
    image = source / project["plan"]["avatar"]["image_path"]
    assert len(str(image)) == 251 and image.is_file()
    original = image.read_bytes()
    original_copy = api.shutil.copytree
    staging_names = []
    def observe_copy(source_path, destination_path, *args, **kwargs):
        if Path(destination_path).parent == source.parent:
            staging_names.append(Path(destination_path).name)
        return original_copy(source_path, destination_path, *args, **kwargs)
    monkeypatch.setattr(api.shutil, "copytree", observe_copy)
    changed = copy.deepcopy(project["plan"])
    changed["postproduction"]["trim_pauses"] = True
    updated = save(client, project, changed)
    destination = storage / "1" / project["id"] / f"revision-{updated['revision']}"
    assert (destination / project["plan"]["avatar"]["image_path"]).read_bytes() == original
    assert image.read_bytes() == original
    assert len(staging_names) == 1 and len(staging_names[0]) <= len(source.name)
    assert list(source.parent.glob(".r????????")) == []
    assert len(runtime.calls) == 1
