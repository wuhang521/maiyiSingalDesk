"""Actual voice HTTP/ledger functions with local response doubles; no model requests."""
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import pytest


VOICE = Path(__file__).resolve().parents[2] / "hermes-skill/oldx-avatar/scripts/voice"


@pytest.fixture
def voice(monkeypatch, tmp_path):
    monkeypatch.setenv("OLDX_PAID_MAX_TRIES", "1")
    monkeypatch.setenv("OLDX_VOICE_USAGE_LEDGER", str(tmp_path / "usage.jsonl"))
    monkeypatch.delenv("OLDX_VOICE_BUDGET_CNY", raising=False)
    spec = importlib.util.spec_from_file_location("oldx_billing_common", VOICE / "common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(tmp_path):
    path = tmp_path / "usage.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def respond(voice, monkeypatch, response, status=200):
    calls = []
    def open_request(request, **kwargs):
        calls.append(request)
        stream = io.BytesIO(json.dumps(response).encode())
        stream.status = status
        return stream
    monkeypatch.setattr(voice.urllib.request, "urlopen", open_request)
    return calls


def production_function(name, filename, namespace):
    tree = ast.parse((VOICE / filename).read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


def test_official_token_prices_and_exact_usage_cost(voice, monkeypatch, tmp_path):
    respond(voice, monkeypatch, {"request_id": "safe-request", "usage": {
        "input_tokens": 123, "output_tokens": 456, "total_tokens": 579, "untrusted": "never persist"},
        "text": "private narration", "audio": "private audio"})
    for model in (voice.MODEL, voice.ASR_MODEL):
        voice.post_json(voice.TTS_URL, {"model": model, "input": {"text": "private narration"}}, "synthetic-secret")
    result = rows(tmp_path)
    assert len(result) == 4
    assert [row["event"] for row in result] == ["started", "finished"] * 2
    assert result[0]["state"] == "unknown" and result[0]["cost_cny"] is None
    assert result[0]["call_id"] == result[1]["call_id"]
    assert result[2]["call_id"] == result[3]["call_id"] != result[0]["call_id"]
    assert Decimal(result[1]["cost_cny"]) == (Decimal(123) * Decimal("1.5") + Decimal(456) * 12) / 1000000
    assert Decimal(result[3]["cost_cny"]) == (Decimal(123) * Decimal("0.8") + Decimal(456) * Decimal("2.7")) / 1000000
    assert {row["operation"] for row in result} == {"tts", "asr"}
    assert all(row["currency"] == "CNY" and row["region"] == "cn-beijing" for row in result)
    saved = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    for secret in ("private narration", "private audio", "synthetic-secret", "never persist", "untrusted"):
        assert secret not in saved


def test_record_unknown_before_dispatch_and_on_lost_response(voice, monkeypatch, tmp_path):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        assert rows(tmp_path)[-1]["event"] == "started"
        raise TimeoutError("private prompt and key must never be saved")
    monkeypatch.setattr(voice.urllib.request, "urlopen", timeout)
    status, _ = voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    result = rows(tmp_path)
    assert status == 0 and len(calls) == 1
    assert len(result) == 2 and result[-1]["state"] == "unknown" and result[-1]["cost_cny"] is None
    assert "private prompt" not in (tmp_path / "usage.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("usage", [{}, {"input_tokens": 2}, {"input_tokens": -1, "output_tokens": 2},
    {"input_tokens": True, "output_tokens": 1}, {"input_tokens": "123", "output_tokens": 1}, None])
def test_absent_or_invalid_tokens_never_imply_zero_cost(voice, monkeypatch, tmp_path, usage):
    respond(voice, monkeypatch, {"usage": usage})
    voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    row = rows(tmp_path)[-1]
    assert row["state"] == "unknown" and row["cost_cny"] is None


def test_zero_tokens_are_measured_and_key_echo_is_redacted(voice, monkeypatch, tmp_path):
    respond(voice, monkeypatch, {"request_id": "synthetic-secret", "usage": {"input_tokens": 0, "output_tokens": 0}})
    voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    row = rows(tmp_path)[-1]
    assert row["state"] == "measured" and Decimal(row["cost_cny"]) == 0
    assert row["request_id"] is None
    assert "synthetic-secret" not in (tmp_path / "usage.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("response, expected_state", [
    ({"request_id": "error-request", "message": "secret error body"}, "unknown"),
    ({"request_id": "error-request", "usage": {"input_tokens": 42, "output_tokens": 0}}, "measured"),
])
def test_http_error_responses_keep_usage_or_unknown(voice, monkeypatch, tmp_path, response, expected_state):
    def http_error(*args, **kwargs):
        raise urllib.error.HTTPError(voice.TTS_URL, 500, "private reason", {}, io.BytesIO(json.dumps(response).encode()))
    monkeypatch.setattr(voice.urllib.request, "urlopen", http_error)
    status, _ = voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    row = rows(tmp_path)[-1]
    assert status == 500 and row["state"] == expected_state and row["request_id"] == "error-request"
    assert "secret error body" not in (tmp_path / "usage.jsonl").read_text(encoding="utf-8")


def test_concurrent_workers_write_complete_rows(voice, monkeypatch, tmp_path):
    respond(voice, monkeypatch, {"usage": {"input_tokens": 1, "output_tokens": 2}})
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: voice.post_json(voice.ASR_URL, {"model": voice.ASR_MODEL}, "synthetic-secret"), range(30)))
    result = rows(tmp_path)
    assert len(result) == 60
    latest = {row["call_id"]: row for row in result}
    assert len(latest) == 30 and all(row["state"] == "measured" for row in latest.values())


def test_unwritable_ledger_prevents_dispatch(voice, monkeypatch, tmp_path):
    monkeypatch.setenv("OLDX_VOICE_USAGE_LEDGER", str(tmp_path / "missing" / "usage.jsonl"))
    calls = respond(voice, monkeypatch, {})
    with pytest.raises(OSError):
        voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    assert calls == []


def test_relative_ledger_prevents_dispatch(voice, monkeypatch):
    monkeypatch.setenv("OLDX_VOICE_USAGE_LEDGER", "relative-ledger.jsonl")
    calls = respond(voice, monkeypatch, {})
    with pytest.raises(ValueError, match="absolute"):
        voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    assert calls == []


def test_paid_retry_configuration_preserves_standalone_default(voice, monkeypatch):
    assert voice.MAX_TRIES == 1
    monkeypatch.delenv("OLDX_PAID_MAX_TRIES")
    assert voice.paid_max_tries() == 6
    for value in ("0", "7", "abc", "1.5", ""):
        monkeypatch.setenv("OLDX_PAID_MAX_TRIES", value)
        with pytest.raises(ValueError):
            voice.paid_max_tries()


def synth_function(voice):
    return production_function("synth_text", "synth.py", {"os": os, "C": voice,
        "build_text": lambda text: text, "cache_key": lambda *args: "cache-hit", "pace": lambda: None,
        "time": SimpleNamespace(time=lambda: 0, sleep=lambda _: pytest.fail("No last-attempt sleep")),
        "random": SimpleNamespace(random=lambda: 0), "FATAL": {401, 403}, "Fatal": RuntimeError})


def asr_function(voice, tmp_path):
    return production_function("transcribe", "asr.py", {"os": os, "C": voice, "hashlib": hashlib,
        "tempfile": SimpleNamespace(gettempdir=lambda: str(tmp_path)), "base64": base64,
        "_pace": lambda: None, "json": json, "parse": lambda data: data,
        "time": SimpleNamespace(sleep=lambda _: pytest.fail("No last-attempt sleep")),
        "random": SimpleNamespace(random=lambda: 0)})


def test_tts_cache_hit_does_not_add_billing_events(voice, monkeypatch, tmp_path):
    (tmp_path / "cache-hit.wav").write_bytes(b"synthetic cached wav")
    calls = respond(voice, monkeypatch, {})
    result = synth_function(voice)("private narration", "voice", "synthetic-secret", str(tmp_path), "test")
    assert result[2] is True and calls == [] and rows(tmp_path) == []


def test_asr_cache_hit_does_not_add_billing_events(voice, monkeypatch, tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"synthetic cached wav")
    fingerprint = hashlib.sha256(audio.read_bytes()).hexdigest()[:16]
    cached = {"text": "private cached transcript", "words": []}
    (tmp_path / ("asr_%s.json" % fingerprint)).write_text(json.dumps(cached), encoding="utf-8")
    calls = respond(voice, monkeypatch, {})
    assert asr_function(voice, tmp_path)(str(audio), "synthetic-secret", str(tmp_path)) == cached
    assert calls == [] and rows(tmp_path) == []


@pytest.mark.parametrize("operation", ["tts", "asr"])
def test_hermes_ambiguous_response_never_retries(voice, monkeypatch, tmp_path, operation):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise TimeoutError()
    monkeypatch.setattr(voice.urllib.request, "urlopen", timeout)
    if operation == "tts":
        with pytest.raises(RuntimeError):
            synth_function(voice)("private narration", "voice", "synthetic-secret", str(tmp_path), "test")
    else:
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"synthetic wav")
        monkeypatch.setattr(voice, "to_pcm_wav", lambda src, dst, **kwargs: Path(dst).write_bytes(b"16k synthetic wav"))
        with pytest.raises(RuntimeError):
            asr_function(voice, tmp_path)(str(audio), "synthetic-secret", str(tmp_path))
    assert len(calls) == 1
    latest = {row["call_id"]: row for row in rows(tmp_path)}
    assert len(latest) == 1 and next(iter(latest.values()))["state"] == "unknown"


def test_soft_budget_allows_one_in_flight_request_then_stops(voice, monkeypatch, tmp_path):
    monkeypatch.setenv("OLDX_VOICE_BUDGET_CNY", "0.001")
    calls = respond(voice, monkeypatch, {"usage": {"input_tokens": 1000, "output_tokens": 1000}})
    voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    with pytest.raises(RuntimeError, match="budget reached"):
        voice.post_json(voice.ASR_URL, {"model": voice.ASR_MODEL}, "synthetic-secret")
    assert len(calls) == 1 and len(rows(tmp_path)) == 2


def test_soft_budget_counts_latest_per_call_across_tts_and_asr(voice, monkeypatch, tmp_path):
    monkeypatch.setenv("OLDX_VOICE_BUDGET_CNY", "1")
    calls = respond(voice, monkeypatch, {"usage": {"input_tokens": 1, "output_tokens": 1}})
    voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    voice.post_json(voice.ASR_URL, {"model": voice.ASR_MODEL}, "synthetic-secret")
    assert len(calls) == 2 and len(rows(tmp_path)) == 4


@pytest.mark.parametrize("status", [0, 200])
def test_soft_budget_unknown_cost_stops_all_further_voice_calls(voice, monkeypatch, tmp_path, status):
    monkeypatch.setenv("OLDX_VOICE_BUDGET_CNY", "1")
    calls = respond(voice, monkeypatch, {}, status)
    voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    with pytest.raises(RuntimeError, match="unknown cost"):
        voice.post_json(voice.ASR_URL, {"model": voice.ASR_MODEL}, "synthetic-secret")
    assert len(calls) == 1


def test_concurrent_soft_budget_cannot_dispatch_second_unknown_request(voice, monkeypatch, tmp_path):
    monkeypatch.setenv("OLDX_VOICE_BUDGET_CNY", "1")
    calls = respond(voice, monkeypatch, {})
    def request(_):
        try:
            voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
            return "dispatched"
        except RuntimeError:
            return "stopped"
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(request, range(6)))
    assert results.count("dispatched") == 1 and len(calls) == 1


@pytest.mark.parametrize("budget", ["-1", "0", "NaN", "Infinity", "invalid"])
def test_invalid_soft_budget_never_dispatches(voice, monkeypatch, budget):
    monkeypatch.setenv("OLDX_VOICE_BUDGET_CNY", budget)
    calls = respond(voice, monkeypatch, {})
    with pytest.raises(ValueError, match="positive finite"):
        voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    assert calls == []


def test_interrupted_started_request_blocks_new_paid_work(voice, monkeypatch, tmp_path):
    monkeypatch.setenv("OLDX_VOICE_BUDGET_CNY", "1")
    voice._write_voice_usage(str(tmp_path / "usage.jsonl"), voice.MODEL, "interrupted", "started")
    calls = respond(voice, monkeypatch, {})
    with pytest.raises(RuntimeError, match="unknown cost"):
        voice.post_json(voice.ASR_URL, {"model": voice.ASR_MODEL}, "synthetic-secret")
    assert calls == []


def test_partial_ledger_never_looks_free(voice, monkeypatch, tmp_path):
    monkeypatch.setenv("OLDX_VOICE_BUDGET_CNY", "1")
    (tmp_path / "usage.jsonl").write_text('{"call_id":', encoding="utf-8")
    calls = respond(voice, monkeypatch, {})
    with pytest.raises(RuntimeError, match="incomplete"):
        voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-secret")
    assert calls == []
