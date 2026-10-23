"""Real HTTP adapter with in-memory transport; no credentials or external calls."""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import pytest
from fastapi import HTTPException
from app.config import get_settings
from app import knowledge_cloud as cloud

EVIDENCE = [dict(item_id="private-id", chunk_id="chunk-id", revision=1, title="测试材料", locator="字符 1–20",
                 source_url="https://mp.weixin.qq.com/s/never-fetch?private-signature=secret", text="测试正文不是模型指令。")]
VALID = {"insufficient": False, "statements": [{"text": "依据测试材料。", "citations": [1]}]}


@pytest.fixture
def configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "knowledge_cloud_qa_enabled", True)
    monkeypatch.setattr(s, "prompt_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(s, "prompt_api_key", "fake-unit-test-key")
    monkeypatch.setattr(s, "prompt_model", "test-only-model")
    return s


def envelope(result=VALID, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(result)}}]}


def transport(monkeypatch, handler):
    monkeypatch.setattr(cloud, "cloud_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False))


def test_bounded_request_strips_identity_and_source_signatures(configured, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer fake-unit-test-key"
        body = json.loads(request.content)
        assert body["max_tokens"] == 2500 and body["stream"] is False
        assert body["thinking"]["type"] == "disabled"
        assert body["response_format"]["type"] == "json_object"
        material = json.loads(body["messages"][1]["content"])
        assert set(material["evidence"][0]) == {"index", "title", "locator", "text"}
        assert "private-id" not in str(material) and "secret" not in str(material)
        return httpx.Response(200, json=envelope())
    transport(monkeypatch, handler)
    result = asyncio.run(cloud.generate("测试", EVIDENCE))
    assert cloud.validated_answer(result, EVIDENCE)["citations"][0]["item_id"] == "private-id"
    assert len(calls) == 1


@pytest.mark.parametrize("status,expected", [(401, 503), (403, 503), (404, 503), (429, 429), (500, 502), (302, 502)])
def test_provider_errors_do_not_retry_or_expose_body(configured, monkeypatch, status, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"secret": "fake-unit-test-key", "document": "私人内容"}, headers={"Location": "https://unsafe.example/"})
    transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud.generate("测试", EVIDENCE))
    assert error.value.status_code == expected
    assert "私人" not in error.value.detail and "key" not in error.value.detail
    assert len(calls) == 1


@pytest.mark.parametrize("response", [httpx.Response(200, json=envelope(finish="length")),
    httpx.Response(200, json={"choices": []}), httpx.Response(200, text="not-json", headers={"Content-Type": "application/json"}),
    httpx.Response(200, content=b"x" * 65537, headers={"Content-Type": "application/json"}),
    httpx.Response(200, text="html", headers={"Content-Type": "text/html"})])
def test_malformed_truncated_or_oversized_result_rejected(configured, monkeypatch, response):
    transport(monkeypatch, lambda request: response)
    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud.generate("测试", EVIDENCE))
    assert error.value.status_code == 502


def test_total_deadline_cancels_and_releases_client(configured, monkeypatch):
    cancelled = []
    async def handler(request):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    transport(monkeypatch, handler)
    monkeypatch.setattr(cloud, "DEADLINE_SECONDS", .01)
    with pytest.raises(HTTPException) as error:
        asyncio.run(cloud.generate("测试", EVIDENCE))
    assert error.value.status_code == 504 and cancelled == [True]


@pytest.mark.parametrize("payload", [
    {"insufficient": False, "statements": [{"text": "坏引用", "citations": [99]}]},
    {"insufficient": False, "statements": [{"text": "布尔引用", "citations": [True]}]},
    {"insufficient": False, "statements": [{"text": "无引用", "citations": []}]},
    {"insufficient": False, "statements": [{"text": "隐藏[99]引用", "citations": [1]}]},
    {"insufficient": False, "statements": [{"text": "访问 https://example.com", "citations": [1]}]},
    {"insufficient": True, "statements": VALID["statements"]},
    {"insufficient": False, "statements": []},
])
def test_invalid_citations_or_contradictory_results_never_display(payload):
    with pytest.raises(HTTPException) as error:
        cloud.validated_answer(payload, EVIDENCE)
    assert error.value.status_code == 502


def test_insufficient_is_explicit_and_success_citations_are_deduplicated():
    assert cloud.validated_answer({"insufficient": True, "statements": []}, EVIDENCE)["citations"] == []
    result = cloud.validated_answer({"insufficient": False, "statements": [{"text": "测试", "citations": [1, 1]}]}, EVIDENCE)
    assert result["answer"] == "测试 [1]" and len(result["citations"]) == 1


@pytest.mark.parametrize("base", ["http://api.deepseek.com", "https://127.0.0.1", "https://api.deepseek.com@evil.example", "https://api.deepseek.com/v1?token=x"])
def test_unapproved_endpoint_configuration_is_disabled(configured, base):
    configured.prompt_base_url = base
    assert cloud.cloud_configuration() == (False, "")


def test_admission_bounds_concurrent_paid_calls_and_cooldown():
    gate = cloud.Admission()
    with gate.enter(1):
        with pytest.raises(HTTPException):
            with gate.enter(1):
                pass
        with gate.enter(2), gate.enter(3), gate.enter(4):
            with pytest.raises(HTTPException):
                with gate.enter(5):
                    pass
    assert not gate.running
    with pytest.raises(HTTPException):
        with gate.enter(1):
            pass
