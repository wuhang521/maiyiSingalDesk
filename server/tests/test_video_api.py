import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace


SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

os.environ.setdefault("TASK_DATABASE_URL", "sqlite:///./test-signaldesk-video.db")
os.environ.setdefault("TASK_API_KEY", "test-key")

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest

from app.config import get_settings
from app.main import app
from app import video
from app.video_pricing import actual_scene_cost, official_scene_quote


HEADERS = {"X-SignalDesk-Key": "test-key"}


def test_pinned_seedance_submission_and_query_keep_official_mini_route(monkeypatch):
    settings = SimpleNamespace(seedance_api_key="synthetic-offline-key", seedance_base_url=video.SEEDANCE_BASE_URL, seedance_model=video.SEEDANCE_MODEL)
    payload = video.GenerationCreate(prompt="科学示意", duration=6)
    spec = video._build_generation_spec(payload, settings)
    settings.seedance_base_url, settings.seedance_model = "https://changed.example/api/v3", "changed-model"
    monkeypatch.setattr(video, "get_settings", lambda: settings)
    calls = []
    async def post(url, key, body):
        calls.append(("post", url, body))
        return {"id": "offline-pinned-task"}
    async def get(url, key):
        calls.append(("get", url, None))
        return {"id": "offline-pinned-task", "status": "running"}
    monkeypatch.setattr(video, "_post_json", post)
    monkeypatch.setattr(video, "_get_json", get)
    assert asyncio.run(video._submit_generation(payload, execution_spec=spec))["id"] == "offline-pinned-task"
    assert asyncio.run(video._query_generation("offline-pinned-task", execution_spec=spec))["status"] == "running"
    assert all(url.startswith(video.SEEDANCE_BASE_URL + video.SEEDANCE_ROUTE) for _, url, _ in calls)
    assert calls[0][2]["model"] == video.SEEDANCE_MODEL and calls[0][2]["generate_audio"] is False


@pytest.mark.parametrize("change", ["base_url", "model", "body", "fingerprint"])
def test_seedance_pinned_request_tampering_is_rejected_without_http(monkeypatch, change):
    settings = SimpleNamespace(seedance_api_key="synthetic-offline-key", seedance_base_url=video.SEEDANCE_BASE_URL, seedance_model=video.SEEDANCE_MODEL)
    payload = video.GenerationCreate(prompt="科学示意", duration=6)
    spec = video._build_generation_spec(payload, settings)
    if change == "body": spec["body"]["duration"] = 7
    elif change == "fingerprint": spec["request_fingerprint"] = "0" * 64
    else: spec[change] = "changed"
    monkeypatch.setattr(video, "get_settings", lambda: settings)
    monkeypatch.setattr(video, "_post_json", lambda *_: pytest.fail("Tampered request must not reach HTTP"))
    with pytest.raises(HTTPException, match="固定生成参数"):
        asyncio.run(video._submit_generation(payload, execution_spec=spec))


def test_create_seedance_generation_maps_reference_images(monkeypatch) -> None:
    captured = {}

    async def fake_post(url, api_key, payload):
        captured.update({"url": url, "api_key": api_key, "payload": payload})
        return {"id": "cgt-test-1"}

    settings = get_settings()
    settings.seedance_api_key = "seedance-secret"
    settings.seedance_base_url = "https://ark.example/api/v3"
    settings.seedance_model = "doubao-seedance-2-0-mini-260615"
    monkeypatch.setattr(video, "_post_json", fake_post)

    payload = video.GenerationCreate(prompt="竖屏镜头，参考@图片1。", duration=6,
        references=[video.ReferenceImage(name="start.png", data_url="data:image/png;base64,AAAA", role="first_frame"),
                    video.ReferenceImage(name="虚拟人物", data_url="asset://asset-20260904-avatar")])
    result = asyncio.run(video._submit_generation(payload))
    assert result == {"id": "cgt-test-1", "status": "queued"}
    assert captured["url"].endswith("/contents/generations/tasks")
    assert captured["api_key"] == "seedance-secret"
    assert captured["payload"]["model"] == "doubao-seedance-2-0-mini-260615"
    assert captured["payload"]["duration"] == 6
    assert captured["payload"]["content"][1]["role"] == "first_frame"
    assert captured["payload"]["content"][2]["image_url"]["url"] == "asset://asset-20260904-avatar"
    assert captured["payload"]["generate_audio"] is False


def test_seedance_duration_is_limited() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/video/generations",
            headers=HEADERS,
            json={"prompt": "test", "duration": 20, "resolution": "720p", "ratio": "9:16"},
        )
    assert response.status_code == 422


def test_compose_prompt_uses_skill_and_returns_json(monkeypatch) -> None:
    captured = {}

    async def fake_post(url, api_key, payload):
        captured.update({"url": url, "payload": payload})
        return {"choices": [{"message": {"content": '{"narration":"新的旁白"}'}}]}

    settings = get_settings()
    settings.prompt_api_key = "prompt-secret"
    settings.prompt_model = "prompt-endpoint-id"
    monkeypatch.setattr(video, "_post_json", fake_post)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/video/prompts/compose",
            headers=HEADERS,
            json={
                "target": "scene.narration",
                "instruction": "更口语化",
                "context": {"current_scene": {"narration": "原旁白"}},
                "locked_fields": [],
            },
        )

    assert response.status_code == 200
    assert response.json()["value"] == {"narration": "新的旁白"}
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "prompt-endpoint-id"
    assert "Video Prompt Director" in captured["payload"]["messages"][0]["content"]


def test_query_seedance_generation_normalizes_result(monkeypatch) -> None:
    async def fake_get(url, api_key):
        return {
            "id": "cgt-test-2",
            "status": "succeeded",
            "content": {"video_url": "https://example.test/result.mp4"},
            "usage": {"completion_tokens": 129600, "total_tokens": 129600,
                      "provider_secret": "do not retain", "cost": 12.3},
            "duration": 6,
            "framespersecond": 24,
            "resolution": "720p",
            "ratio": "9:16",
            "model": "doubao-seedance-2-0-mini-260615",
        }

    settings = get_settings()
    settings.seedance_api_key = "seedance-secret"
    monkeypatch.setattr(video, "_get_json", fake_get)

    result = asyncio.run(video._query_generation("cgt-test-2"))
    assert result["status"] == "succeeded"
    assert result["videoUrl"] == "https://example.test/result.mp4"
    assert result["usage"] == {"completion_tokens": 129600, "total_tokens": 129600}
    assert result["duration"] == 6
    assert result["fps"] == 24
    assert result["resolution"] == "720p"
    assert result["ratio"] == "9:16"
    assert result["model"] == "doubao-seedance-2-0-mini-260615"


@pytest.mark.parametrize("usage", [None, [], {"completion_tokens": True},
    {"completion_tokens": "42"}, {"completion_tokens": -1},
    {"completion_tokens": float("nan")}, {"completion_tokens": 10**20}])
def test_query_seedance_discards_unsafe_accounting_metadata(monkeypatch, usage):
    async def fake_get(url, api_key):
        return {"status": "succeeded", "usage": usage, "duration": True,
                "framespersecond": float("inf"), "ratio": "unknown",
                "resolution": "invalid", "model": "secret with spaces"}
    monkeypatch.setattr(video, "_get_json", fake_get)
    get_settings().seedance_api_key = "synthetic-key"
    result = asyncio.run(video._query_generation("synthetic-task"))
    assert result["usage"] == {}
    assert all(result[field] is None for field in ("duration", "fps", "ratio", "resolution", "model"))


def test_official_mini_scene_quote_uses_current_list_price_and_reserve():
    quote = official_scene_quote(6, "720p", "9:16")
    assert quote["amount"] == 2.9808
    assert quote["reservation_amount"] == 4.1732
    assert quote["estimated_tokens"] == 129600
    assert quote["unit_price"] == 23
    assert quote["currency"] == "CNY"
    assert quote["pricing_kind"] == "estimate"
    assert quote["verified_at"] == "2026-10-22"
    assert quote["source_url"].startswith("https://docs.volcengine.com/")
    assert "非供应商保证上限" in quote["basis"]


@pytest.mark.parametrize("resolution,ratio,expected", [
    ("480p", "9:16", 50220), ("480p", "16:9", 50220),
    ("720p", "9:16", 108000), ("720p", "4:3", 108681),
    ("720p", "21:9", 108528), ("480p", "1:1", 48000),
    ("480p", "3:4", 49350),
])
def test_official_mini_quote_maps_actual_output_pixels(resolution, ratio, expected):
    assert official_scene_quote(5, resolution, ratio)["estimated_tokens"] == expected


@pytest.mark.parametrize("seconds,resolution,ratio", [
    (3, "720p", "9:16"), (16, "720p", "9:16"),
    (4.5, "720p", "9:16"), (True, "720p", "9:16"),
    (float("nan"), "720p", "9:16"), (5, "1080p", "9:16"),
    (5, "720p", "adaptive"), (5, "720p", "unsupported"),
])
def test_official_mini_quote_rejects_unpriced_specifications(seconds, resolution, ratio):
    with pytest.raises(ValueError):
        official_scene_quote(seconds, resolution, ratio)


@pytest.mark.parametrize("usage,expected", [
    ({"completion_tokens": 129600}, 2.9808), ({"completion_tokens": 0}, 0),
    ({"completion_tokens": 1}, 0.0001), ({"total_tokens": 129600}, None),
    ({"completion_tokens": "129600"}, None), ({"completion_tokens": True}, None),
    ({"completion_tokens": -1}, None), ({"completion_tokens": float("inf")}, None),
    (None, None), ([], None),
])
def test_actual_scene_cost_preserves_unknown_usage(usage, expected):
    assert actual_scene_cost(usage) == expected


def test_legacy_generation_routes_cannot_bypass_project_budget(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Provider must not be contacted")
    monkeypatch.setattr(video, "_post_json", forbidden)
    monkeypatch.setattr(video, "_get_json", forbidden)
    with TestClient(app) as client:
        assert client.post("/api/v1/video/generations", headers=HEADERS,
            json={"prompt":"test", "duration":6}).status_code == 410
        assert client.get("/api/v1/video/generations/old-task", headers=HEADERS).status_code == 410
