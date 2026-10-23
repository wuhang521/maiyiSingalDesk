"""RunningHub Mini contract tests with synthetic credentials and HTTP transports."""

import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys

from fastapi import HTTPException
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import video_runninghub as rh


@pytest.fixture(autouse=True)
def isolated_credentials_and_cache(monkeypatch):
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "synthetic-rh-key")
    monkeypatch.delenv("RH_KEY_FILE", raising=False)
    rh._QUOTE_CACHE.clear()
    yield
    rh._QUOTE_CACHE.clear()


def spec(references=None, duration=6, resolution="720p"):
    return rh.build_scene_spec({"visual_intent": "水循环科学动画", "duration": duration,
                                "references": references or []}, {"resolution": resolution})


def transport(monkeypatch, payloads):
    requests = []
    values = iter(payloads)
    def handler(request):
        requests.append(request)
        value = next(values)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)
    mock = httpx.MockTransport(handler)
    client, async_client = httpx.Client, httpx.AsyncClient
    def sync_factory(**kwargs):
        assert kwargs == {"timeout": 12, "follow_redirects": False, "trust_env": False}
        return client(transport=mock, **kwargs)
    def async_factory(**kwargs):
        assert kwargs == {"timeout": 12, "follow_redirects": False, "trust_env": False}
        return async_client(transport=mock, **kwargs)
    monkeypatch.setattr(rh.httpx, "Client", sync_factory)
    monkeypatch.setattr(rh.httpx, "AsyncClient", async_factory)
    return requests


def test_minimal_official_text_payload_and_constraints():
    value = spec()
    assert value["provider"] == "runninghub" and value["model"] == "sparkvideo-2.0-mini"
    assert value["route"] == rh.T2V_ROUTE
    assert value["body"] == {"prompt": "水循环科学动画", "resolution": "720p", "duration": "6",
        "ratio": "9:16", "generateAudio": False, "returnLastFrame": False, "seed": -1, "webSearch": False}
    assert value["request_fingerprint"] == spec()["request_fingerprint"]
    assert rh.scene_constraints()["duration_step"] == 1


def test_official_first_and_last_frame_mapping():
    value = spec([{"role": "first_frame", "data_url": "https://images.example/first.png"},
                  {"role": "last_frame", "data_url": "https://images.example/last.png"}])
    assert value["route"] == rh.I2V_ROUTE
    assert value["body"]["firstFrameUrl"] == "https://images.example/first.png"
    assert value["body"]["lastFrameUrl"] == "https://images.example/last.png"
    assert value["body"]["realPersonMode"] is False
    assert "webSearch" not in value["body"]


@pytest.mark.parametrize("references", [
    [{"role": "reference_image", "data_url": "https://images.example/image.png"}],
    [{"data_url": "https://images.example/image.png"}],
    [{"role": "last_frame", "data_url": "https://images.example/image.png"}],
    [{"role": "first_frame", "data_url": "https://images.example/image.png"}] * 2,
    [{"role": "first_frame", "data_url": "https://images.example/image.png"}] * 3,
])
def test_unsupported_reference_roles_are_never_silently_dropped(references):
    with pytest.raises(HTTPException) as error:
        spec(references)
    assert error.value.status_code == 422


@pytest.mark.parametrize("url", ["data:image/png;base64,AAAA", "asset://a", "file:///image.png",
    "http://example.com/image.png", "https://127.0.0.1/a", "https://10.0.0.1/a", "https://localhost/a",
    "https://user:secret@example.com/a", "https://images.local/a", "https://example.com/a?apiKey=secret"])
def test_unsupported_image_locations_fail_closed(url):
    with pytest.raises(HTTPException) as error:
        spec([{"role": "first_frame", "data_url": url}])
    assert error.value.status_code == 422


@pytest.mark.parametrize("duration", [True, 3, 16, -1, 5.5, "6", None])
def test_only_fixed_integer_four_to_fifteen_seconds_are_allowed(duration):
    with pytest.raises(HTTPException):
        spec(duration=duration)


@pytest.mark.parametrize("resolution", ["1080p", "4k", "auto"])
def test_only_native_resolutions_are_exposed(resolution):
    with pytest.raises(HTTPException):
        spec(resolution=resolution)


def test_quote_and_submit_use_exact_same_official_payload_and_bearer(monkeypatch):
    expected = spec()
    seen = transport(monkeypatch, [{"errorCode": "", "estimatedPrice": 1.23456, "currency": "CNY",
        "rawSecret": "must-not-return"}, {"taskId": "task-1", "status": "RUNNING", "errorCode": ""}])
    quote = rh.quote_scene(expected)
    submitted = asyncio.run(rh.submit_scene(expected))
    assert [json.loads(request.content) for request in seen] == [expected["body"], expected["body"]]
    assert str(seen[0].url) == rh.BASE_URL + expected["route"].replace("/openapi/v2/", "/openapi/v2/price-preview/")
    assert str(seen[1].url) == rh.BASE_URL + expected["route"]
    assert all(request.headers["Authorization"] == "Bearer synthetic-rh-key" for request in seen)
    assert quote["amount"] == 1.2346 and quote["reservation_amount"] == 1.4815
    assert quote["provider_hard_cap"] is False
    assert (datetime.fromisoformat(quote["expires_at"]) - datetime.fromisoformat(quote["quoted_at"])).total_seconds() == 300
    assert submitted["id"] == "task-1" and submitted["submission_spec"] == expected
    assert "must-not-return" not in json.dumps(quote) and "synthetic-rh-key" not in json.dumps(submitted)


def test_quote_cache_is_key_and_spec_bound_retains_times_and_cannot_be_mutated(monkeypatch):
    seen = transport(monkeypatch, [{"estimatedPrice": 1, "currency": "CNY"}] * 4)
    first = rh.quote_scene(spec())
    cached = rh.quote_scene(spec())
    assert cached == first and len(seen) == 1
    first["amount"] = 999
    assert rh.quote_scene(spec())["amount"] == 1
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "another-synthetic-key")
    rh.quote_scene(spec())
    rh.quote_scene(spec(duration=7))
    assert len(seen) == 3
    assert all("synthetic-key" not in str(key) for key in rh._QUOTE_CACHE)
    for row in rh._QUOTE_CACHE.values():
        row["expires_mono"] = -1
    rh.quote_scene(spec(duration=7))
    assert len(seen) == 4


def test_quote_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(rh, "_QUOTE_CACHE_MAX", 2)
    seen = transport(monkeypatch, [{"estimatedPrice": 1, "currency": "CNY"}] * 3)
    for duration in (4, 5, 6):
        rh.quote_scene(spec(duration=duration))
    assert len(rh._QUOTE_CACHE) == 2 and len(seen) == 3


@pytest.mark.parametrize("payload", [{"estimatedPrice": 1, "currency": "RH"},
    {"estimatedPrice": "NaN", "currency": "CNY"}, {"estimatedPrice": "Infinity", "currency": "CNY"},
    {"estimatedPrice": -1, "currency": "CNY"}, {"estimatedPrice": True, "currency": "CNY"},
    {"estimatedPrice": 1, "currency": "CNY", "errorCode": "secret-denial"},
    {"currency": "CNY"}, []])
def test_bad_official_quote_is_unknown_never_free_and_not_cached(monkeypatch, payload):
    seen = transport(monkeypatch, [payload])
    with pytest.raises(HTTPException) as error:
        rh.quote_scene(spec())
    assert error.value.status_code == 503 and len(seen) == 1
    assert "secret-denial" not in error.value.detail
    assert not rh._QUOTE_CACHE


def test_exact_zero_official_quote_is_accepted(monkeypatch):
    transport(monkeypatch, [{"estimatedPrice": 0, "currency": "CNY", "errorCode": ""}])
    value = rh.quote_scene(spec())
    assert value["amount"] == 0 and "不代表保证免费" in value["basis"]


def test_actual_usd_quote_stays_in_original_currency_without_conversion(monkeypatch):
    transport(monkeypatch, [{"errorCode": "", "estimatedPrice": 0.6, "currency": "USD", "estimatedPoint": 600}])
    value = rh.quote_scene(spec())
    assert value["amount"] == 0.6 and value["reservation_amount"] == 0.72 and value["currency"] == "USD"
    assert "官方以美元报价，不代表人民币，未做换汇" in value["basis"]
    assert "estimatedPoint" not in value


@pytest.mark.parametrize("mutation", ["route", "body", "fingerprint"])
def test_caller_cannot_inject_endpoint_or_modify_confirmed_body(monkeypatch, mutation):
    expected = spec()
    if mutation == "route":
        expected["route"] = "https://attacker.invalid/upload"
    elif mutation == "body":
        expected["body"]["generateAudio"] = True
        expected["request_fingerprint"] = rh._fingerprint(expected["route"], expected["body"])
    else:
        expected["request_fingerprint"] = "0" * 64
    seen = transport(monkeypatch, [])
    with pytest.raises(HTTPException) as error:
        rh.quote_scene(expected)
    assert error.value.status_code == 422 and seen == []


def test_key_file_fallback_and_environment_precedence(monkeypatch, tmp_path):
    file = tmp_path / "private-key"
    file.write_text("synthetic-file-key\n", encoding="utf-8")
    monkeypatch.setenv("RH_KEY_FILE", str(file))
    assert rh._credential() == "synthetic-rh-key"
    monkeypatch.delenv("RUNNINGHUB_API_KEY")
    assert rh._credential() == "synthetic-file-key"


def test_ark_key_is_never_a_runninghub_fallback(monkeypatch):
    monkeypatch.delenv("RUNNINGHUB_API_KEY")
    monkeypatch.setenv("ARK_API_KEY", "synthetic-ark-key")
    with pytest.raises(HTTPException) as error:
        rh._credential()
    assert error.value.status_code == 503 and "synthetic-ark-key" not in error.value.detail


def test_submission_transport_failure_has_no_retry_and_never_leaks_secret(monkeypatch):
    seen = transport(monkeypatch, [httpx.ConnectError("Authorization: Bearer synthetic-rh-key")])
    with pytest.raises(HTTPException) as error:
        asyncio.run(rh.submit_scene(spec()))
    assert len(seen) == 1
    assert "synthetic-rh-key" not in str(error.value)


def test_failed_query_keeps_safe_billing_and_never_provider_errors(monkeypatch):
    seen = transport(monkeypatch, [{"taskId": "task-1", "status": "FAILED", "errorCode": "raw-secret",
        "errorMessage": "Bearer synthetic-rh-key", "usage": {"consumeMoney": "1.25", "thirdPartyConsumeMoney": "0.1",
        "consumeCoins": "NaN", "taskCostTime": "Infinity", "Authorization": "synthetic-rh-key"}}])
    result = asyncio.run(rh.query_scene("task-1"))
    assert json.loads(seen[0].content) == {"taskId": "task-1"}
    assert str(seen[0].url) == rh.BASE_URL + "/openapi/v2/query"
    assert result["status"] == "failed" and result["provider"] == "runninghub"
    assert result["usage"] == {"consumeMoney": "1.25", "thirdPartyConsumeMoney": "0.1"}
    assert "synthetic-rh-key" not in json.dumps(result) and "raw-secret" not in json.dumps(result)


@pytest.mark.parametrize("status,expected", [("QUEUED", "queued"), ("RUNNING", "running"), ("SUCCESS", "succeeded")])
def test_query_normalizes_states_and_only_mp4_https_outputs(monkeypatch, status, expected):
    transport(monkeypatch, [{"status": status, "results": [{"outputType": "png", "url": "https://example.com/a.png"},
        {"outputType": "mp4", "url": "http://127.0.0.1/private"},
        {"outputType": "mp4", "url": "https://videos.example/result.mp4"}], "usage": {"consumeMoney": 0}}])
    result = asyncio.run(rh.query_scene("task-1"))
    assert result["status"] == expected
    assert result["videoUrl"] == "https://videos.example/result.mp4"
    assert result["usage"] == {"consumeMoney": "0"}


def test_malformed_task_id_does_not_make_request(monkeypatch):
    seen = transport(monkeypatch, [])
    with pytest.raises(HTTPException):
        asyncio.run(rh.query_scene("../other?apiKey=secret"))
    assert seen == []


@pytest.mark.parametrize("currency,expected", [("CNY", "CNY"), ("RH", "RH"), ("USD", "USD"), ("EUR", "UNKNOWN"),
                                              (None, None), ("", "UNKNOWN")])
def test_query_preserves_explicit_currency_without_assuming_cny(monkeypatch, currency, expected):
    transport(monkeypatch, [{"status": "SUCCESS", "currency": currency, "usage": {"consumeMoney": "0.1"}}])
    assert asyncio.run(rh.query_scene("task-1"))["currency"] == expected


def test_immediately_failed_submit_keeps_safe_usage_and_currency(monkeypatch):
    seen = transport(monkeypatch, [{"taskId": "task-1", "status": "FAILED", "errorCode": "raw-secret",
        "currency": "RH", "errorMessage": "Bearer synthetic-rh-key", "usage": {"consumeCoins": "3",
        "consumeMoney": "NaN", "taskCostTime": "2", "secret": "synthetic-rh-key"}}])
    result = asyncio.run(rh.submit_scene(spec()))
    assert len(seen) == 1 and result["status"] == "failed" and result["currency"] == "RH"
    assert result["usage"] == {"consumeCoins": "3", "taskCostTime": "2"}
    assert "raw-secret" not in json.dumps(result) and "synthetic-rh-key" not in json.dumps(result)


def test_query_http_failure_is_safe_and_never_retried(monkeypatch):
    seen = transport(monkeypatch, [httpx.Response(403, json={"errorMessage": "Bearer synthetic-rh-key"})])
    with pytest.raises(HTTPException) as error:
        asyncio.run(rh.query_scene("task-1"))
    assert len(seen) == 1 and "synthetic-rh-key" not in str(error.value)
