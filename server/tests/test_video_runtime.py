"""Offline provider contracts and real local ffmpeg verification; no live API use."""
import asyncio
import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import video_runtime as runtime


def run(value):
    return asyncio.run(value)


@pytest.mark.skipif(os.name != "nt", reason="Windows inherited stdin pipe regression")
def test_cli_review_completes_with_parent_stdin_reader_and_devnull(tmp_path):
    """Control reproduces the old hang; fixed child finishes while stdin stays open."""
    server = Path(__file__).resolve().parents[1]
    bundle = server.parent / "hermes-skill/oldx-avatar"
    wrapper = r'''
import asyncio, json, os, sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app import video_runtime as runtime
runtime._python = lambda: sys.executable
runtime._bundle = lambda: Path(sys.argv[2])
started = threading.Event()
def read_parent():
    started.set()
    for line in sys.stdin:
        pass
threading.Thread(target=read_parent, daemon=True).start()
started.wait(); time.sleep(.2)
if sys.argv[4] == 'inherited-control':
    original = asyncio.create_subprocess_exec
    async def inherited(*args, **kwargs):
        kwargs.pop('stdin', None)
        return await original(*args, **kwargs)
    runtime.asyncio.create_subprocess_exec = inherited
result = asyncio.run(runtime._cli(Path(sys.argv[3]), 'review.py', 'init', '--workdir', sys.argv[3]))
print(json.dumps({'completed': True, 'review_enabled': result.get('review_enabled')}), flush=True)
os._exit(0)
'''
    environment = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "PATH") if name in os.environ}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    results = {}
    for mode in ("inherited-control", "fixed"):
        directory = tmp_path / mode
        directory.mkdir(); (directory / "copy.txt").write_text("离线测试文案", encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-B", "-u", "-c", wrapper, str(server), str(bundle), str(directory), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        timed_out = False
        try:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                timed_out = True
            # Releasing this test-owned pipe frees the deliberately broken control.
            process.stdin.close()
            process.wait(timeout=10)
            stdout, stderr = process.stdout.read(), process.stderr.read()
            assert process.returncode == 0, stderr.decode("utf-8", "replace")
            assert json.loads(stdout.decode("utf-8"))["review_enabled"] is True
            results[mode] = timed_out
        finally:
            if not process.stdin.closed: process.stdin.close()
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            process.stdout.close(); process.stderr.close()
    assert results == {"inherited-control": True, "fixed": False}


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_local_review_timeout_is_bounded_sanitized_and_never_retried(monkeypatch, tmp_path, cleanup_failure):
    bundle = tmp_path / "bundle"; (bundle / "scripts").mkdir(parents=True)
    script = bundle / "scripts/review.py"
    script.write_text("import sys,time\nprint('synthetic-sensitive-stdout',flush=True)\nprint('synthetic-sensitive-stderr',file=sys.stderr,flush=True)\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "_bundle", lambda: bundle)
    monkeypatch.setattr(runtime, "_python", lambda: getattr(sys, "_base_executable", sys.executable))
    monkeypatch.setattr(runtime, "LOCAL_REVIEW_TIMEOUT_SECONDS", .5)
    if cleanup_failure:
        original_stop = runtime._stop_local_review
        async def stop(process):
            await original_stop(process)
            raise RuntimeError("synthetic-sensitive-cleanup-error")
        monkeypatch.setattr(runtime, "_stop_local_review", stop)
    original = asyncio.create_subprocess_exec
    starts = []
    async def create(*args, **kwargs):
        if str(script) in args:
            starts.append(kwargs.get("stdin"))
        return await original(*args, **kwargs)
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", create)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="本地确认步骤 review.py 超时") as failed:
        run(runtime._cli(tmp_path, "review.py", "init", "--workdir", tmp_path))
    assert "synthetic-sensitive" not in str(failed.value)
    assert starts == [asyncio.subprocess.DEVNULL]
    assert time.monotonic() - started < 10


def test_paid_pipeline_watch_timeout_is_not_reported_as_local_review_timeout(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"; (bundle / "scripts").mkdir(parents=True)
    (bundle / "scripts/pipeline.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "_bundle", lambda: bundle)
    monkeypatch.setattr(runtime, "_python", lambda: getattr(sys, "_base_executable", sys.executable))
    async def watch():
        raise TimeoutError("offline-watch-timeout")
    with pytest.raises(TimeoutError, match="offline-watch-timeout"):
        run(runtime._cli(tmp_path, "pipeline.py", "voice", "--yes", watch=watch))


def test_cancelled_local_review_cleans_its_child_and_communicate(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"; (bundle / "scripts").mkdir(parents=True)
    (bundle / "scripts/review.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "_bundle", lambda: bundle)
    monkeypatch.setattr(runtime, "_python", lambda: getattr(sys, "_base_executable", sys.executable))
    original = asyncio.create_subprocess_exec
    children = []
    async def create(*args, **kwargs):
        child = await original(*args, **kwargs)
        if str(bundle / "scripts/review.py") in args:
            children.append(child)
        return child
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", create)
    async def cancel():
        task = asyncio.create_task(runtime._cli(tmp_path, "review.py", "init", "--workdir", tmp_path))
        while not children:
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
    run(cancel())
    assert children and all(child.returncode is not None for child in children)


def test_local_review_deadline_does_not_apply_to_paid_pipeline(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"; (bundle / "scripts").mkdir(parents=True)
    (bundle / "scripts/pipeline.py").write_text("import time\ntime.sleep(.3)\nprint('{\"ok\":true}')\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "_bundle", lambda: bundle)
    monkeypatch.setattr(runtime, "_python", lambda: getattr(sys, "_base_executable", sys.executable))
    monkeypatch.setattr(runtime, "LOCAL_REVIEW_TIMEOUT_SECONDS", .05)
    assert run(runtime._cli(tmp_path, "pipeline.py", "voice", "--yes"))["ok"] is True


def test_voice_local_preparation_failure_is_distinct_and_does_not_start_paid_step(monkeypatch, tmp_path, project):
    async def unavailable(*args):
        raise RuntimeError("synthetic-sensitive-local-log")
    monkeypatch.setattr(runtime, "_prepare_voice", unavailable)
    calls = []
    result = run(runtime._voice_job(job("voice"), project, tmp_path, calls.append))
    assert result == {"status": "blocked", "result": {"phase": "local_preparation", "paid_request_started": False},
                      "error": "本地配音准备未完成，尚未调用配音模型；请重试"}
    assert calls == [] and not list(tmp_path.rglob("voice-usage*"))


def test_voice_unwritable_ledger_stops_before_submitting_or_pipeline(monkeypatch, tmp_path, project):
    async def prepare(*args):
        return runtime._skill_workdir(tmp_path, project)
    async def forbidden(*args, **kwargs):
        pytest.fail("An unwritable local ledger must stop before any paid pipeline")
    def unavailable(*args, **kwargs):
        raise OSError("synthetic-sensitive-path-error")
    monkeypatch.setattr(runtime, "_prepare_voice", prepare)
    monkeypatch.setattr(runtime, "_cli", forbidden)
    monkeypatch.setattr(runtime.os, "open", unavailable)
    events = []
    result = run(runtime._voice_job(job("voice"), project, tmp_path, events.append))
    assert result == {"status": "blocked", "result": {"phase": "local_preparation", "paid_request_started": False},
                      "error": "本地配音记录路径不可写，尚未调用配音模型；请重试"}
    assert events == []
    assert "synthetic-sensitive" not in json.dumps(result)


def test_voice_ledger_probe_never_truncates_or_adds_billing_rows(monkeypatch, tmp_path, project):
    ledger = tmp_path / "voice-usage-job-voice.jsonl"
    original = b'{"call_id":"existing","state":"unknown","cost_cny":null}\n'
    ledger.write_bytes(original)
    async def prepare(*args):
        return runtime._skill_workdir(tmp_path, project)
    async def cli(wd, script, *args, **kwargs):
        assert Path(kwargs["paid_environment"]["OLDX_VOICE_USAGE_LEDGER"]) == ledger
        assert ledger.read_bytes() == original
        make_wav(wd / "out/merged.wav")
        runtime._dump(wd / "out/words.json", [])
    monkeypatch.setattr(runtime, "_prepare_voice", prepare)
    monkeypatch.setattr(runtime, "_cli", cli)
    result = run(runtime._voice_job(job("voice"), project, tmp_path, None))
    assert result["status"] == "succeeded"
    assert ledger.read_bytes() == original
    assert result["result"]["billing"]["unknown_requests"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH voice ledger regression")
def test_voice_uuid_ledger_avoids_real_windows_260_path_before_mock_http(monkeypatch, tmp_path, project):
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
        if winreg.QueryValueEx(key, "LongPathsEnabled")[0]:
            pytest.skip("Control requires Windows long paths to be disabled")
    padding = 187 - len(str(tmp_path))
    assert padding > 0, "Use a short temporary base for the Windows path regression"
    root = tmp_path / ("r" * padding)
    wd = runtime._skill_workdir(root, project)
    wd.mkdir(parents=True)
    identifier = "8df1ed7d-16cc-49ab-9527-c9fc4e021c6c"
    basename = f"voice-usage-{identifier}.jsonl"
    old_ledger, ledger = wd / basename, root / basename
    assert len(str(wd)) == 205 and len(str(old_ledger)) == 260 and len(str(ledger)) == 243
    spec = importlib.util.spec_from_file_location("offline_voice_long_path_common",
        Path(__file__).resolve().parents[2] / "hermes-skill/oldx-avatar/scripts/voice/common.py")
    voice = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("OLDX_PAID_MAX_TRIES", "1")
    monkeypatch.delenv("OLDX_VOICE_BUDGET_CNY", raising=False)
    spec.loader.exec_module(voice)
    calls = []
    def fake_http(request, **kwargs):
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert rows[-1]["event"] == "started"
        calls.append(request)
        response = io.BytesIO(b'{"request_id":"offline-request","usage":{"input_tokens":1,"output_tokens":2}}')
        response.status = 200
        return response
    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_http)
    monkeypatch.setenv("OLDX_VOICE_USAGE_LEDGER", str(old_ledger))
    with pytest.raises(FileNotFoundError):
        voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-offline-key")
    assert calls == [] and not old_ledger.exists()
    async def prepare(*args):
        return wd
    async def cli(directory, script, *args, **kwargs):
        assert script == "pipeline.py"
        assert ledger.is_file() and ledger.stat().st_size == 0
        monkeypatch.setenv("OLDX_VOICE_USAGE_LEDGER", kwargs["paid_environment"]["OLDX_VOICE_USAGE_LEDGER"])
        assert voice.post_json(voice.TTS_URL, {"model": voice.MODEL}, "synthetic-offline-key")[0] == 200
        make_wav(wd / "out/merged.wav")
        runtime._dump(wd / "out/words.json", [])
    monkeypatch.setattr(runtime, "_prepare_voice", prepare)
    monkeypatch.setattr(runtime, "_cli", cli)
    events = []
    outcome = run(runtime._voice_job(job("voice", id=identifier), project, root, events.append))
    assert outcome["status"] == "succeeded" and len(calls) == 1
    assert events == [{"status": "submitting", "submission_started": True}]
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in rows] == ["started", "finished"]
    assert outcome["result"]["billing"]["complete"] is True


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    # No production key files are inspected and the child environment has no keys.
    for name in ("DASHSCOPE_API_KEY", "RUNNINGHUB_API_KEY", "RH_KEY_FILE", "BL_KEY_FILE", "ARK_API_KEY", "TASK_VIDEO_PYTHON", "TASK_VIDEO_FFMPEG",
                 "TASK_VIDEO_SCENE_COST_PER_SECOND", "TASK_VIDEO_VOICE_COST_PER_1000_CHARS", "TASK_VIDEO_AVATAR_COST_PER_SECOND", "TASK_VIDEO_PAID_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    for name in ("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "TASK_VIDEO_AVATAR_CURRENCY", "TASK_VIDEO_SCENE_CREDENTIAL_SOURCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TASK_VIDEO_PRICING_PROFILE", raising=False)
    monkeypatch.delenv("TASK_VIDEO_SCENE_PROVIDER", raising=False)
    monkeypatch.delenv("TASK_VIDEO_COST_REVIEW_REQUIRED", raising=False)
    monkeypatch.setattr(runtime, "_settings", lambda: SimpleNamespace(prompt_model="", prompt_api_key="", seedance_api_key="", seedance_model="mock"))


@pytest.fixture
def project():
    return {"id": "p1", "mode": "standard", "revision": 1, "topic": "离线演示", "platforms": ["视频号", "抖音", "小红书"],
            "plan": {"brief": {"title": "离线素材测试"}, "audio_plan": {}, "burn_subtitles": True,
                     "scenes": [{"id": "s1", "title": "开场", "duration": 4, "narration": "This is a test.", "seedance_prompt": "mock scene 1"},
                                {"id": "s2", "title": "解释", "duration": 4, "narration": "Here is the answer.", "seedance_prompt": "mock scene 2"}]},
            "reviews": {s: {"revision": 1, "decision": "approved", "quote": f"测试用户确认 {s}"} for s in ("inputs", "voice", "generation", "video")}, "jobs": []}


def job(kind="scene", **extra):
    provider = (extra.get("execution") or {}).get("provider", "seedance")
    return {"id": f"job-{kind}", "kind": kind, "scene_id": "s1", "revision": 1, "status": "queued",
            "result": {"provider": provider},
            "budget_grant": {"approved": True, "revision": 1, "max_cost": 100, "currency": "CNY"}, **extra}


def ready(monkeypatch):
    monkeypatch.setattr(runtime, "runtime_capabilities", lambda: {key: {"ready": True, "reason": ""} for key in ["planning", *runtime.KINDS]})
    monkeypatch.setenv("TASK_VIDEO_SCENE_COST_PER_SECOND", "0.6")
    monkeypatch.setenv("TASK_VIDEO_VOICE_COST_PER_1000_CHARS", "2")
    monkeypatch.setenv("TASK_VIDEO_AVATAR_COST_PER_SECOND", "30")


def provider(monkeypatch):
    # Keep provider tests wholly offline, including avoiding the application's
    # settings/database imports (which could load a production .env file).
    async def unset(*args, **kwargs):
        pytest.fail("Provider method was not explicitly mocked")
    value = SimpleNamespace(GenerationCreate=lambda **kwargs: SimpleNamespace(**kwargs), _submit_generation=unset, _query_generation=unset)
    original = runtime.importlib.import_module
    monkeypatch.setattr(runtime.importlib, "import_module", lambda name, *args: value if name == "app.video" else original(name, *args))
    return value


def test_paid_adapters_are_disabled_by_default(monkeypatch):
    monkeypatch.setattr(runtime, "_ffmpeg", lambda: "")
    caps = runtime.runtime_capabilities()
    assert all(not caps[name]["ready"] for name in ("scene", "voice", "avatar"))
    assert caps["scene"]["reason"] == "本地付费调用未启用"


def test_scene_and_batch_capabilities_report_hermes_credential_source_without_secret(monkeypatch):
    monkeypatch.setattr(runtime, "_ffmpeg", lambda: "")
    monkeypatch.setattr(runtime, "_settings", lambda: SimpleNamespace(prompt_model="", prompt_api_key="", seedance_api_key="synthetic-test-only"))
    monkeypatch.setenv("TASK_VIDEO_SCENE_CREDENTIAL_SOURCE", "hermes")
    caps = runtime.runtime_capabilities()
    assert caps["scene"] == caps["scene_batch"]
    assert caps["scene"]["credentials_configured"] and caps["scene"]["credential_source"] == "hermes"
    assert not caps["scene"]["ready"]
    assert "synthetic-test-only" not in json.dumps(caps)


def seedance_direct(monkeypatch, project, *, kind="scene"):
    from app import video
    settings = SimpleNamespace(prompt_model="", prompt_api_key="", seedance_api_key="synthetic-ark-key",
        seedance_base_url=video.SEEDANCE_BASE_URL, seedance_model=video.SEEDANCE_MODEL)
    monkeypatch.setattr(runtime, "_settings", lambda: settings)
    monkeypatch.setattr(video, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "_ffmpeg", lambda: "")
    monkeypatch.setenv("TASK_VIDEO_PAID_ENABLED", "1")
    monkeypatch.setenv("TASK_VIDEO_COST_REVIEW_REQUIRED", "0")
    monkeypatch.setenv("TASK_VIDEO_SCENE_PROVIDER", "seedance")
    monkeypatch.setenv("TASK_VIDEO_SCENE_CREDENTIAL_SOURCE", "hermes")
    request = {"kind": kind, "scene_id": "s1", **({"scene_ids": ["s1", "s2"]} if kind == "scene_batch" else {})}
    prepared = runtime.prepare_job(request, project)
    value = job(kind, execution=prepared["execution"], estimated_cost=prepared,
        budget_grant={"approved": True, "revision": 1, "generation_consent": True},
        **({"scene_ids": ["s1", "s2"]} if kind == "scene_batch" else {}))
    return video, settings, value


def test_seedance_direct_prepare_and_recovery_are_pinned_without_prices(monkeypatch, tmp_path, project):
    video, settings, value = seedance_direct(monkeypatch, project)
    monkeypatch.setattr(runtime, "estimate_job", lambda *_: pytest.fail("No pricing in direct mode"))
    cap = runtime.runtime_capabilities()["scene"]
    assert cap["ready"] and cap["provider"] == "seedance" and cap["credential_source"] == "hermes"
    assert cap["model"] == video.SEEDANCE_MODEL and cap["base_url"] == video.SEEDANCE_BASE_URL
    assert cap["constraints"]["resolutions"] == ["480p", "720p"]
    calls = []
    async def post(url, key, body):
        calls.append(("post", url, body["model"]))
        return {"id": "ark-pinned-1"}
    async def get(url, key):
        calls.append(("get", url, None))
        return {"id": "ark-pinned-1", "status": "succeeded", "content": {"video_url": "https://media.example.test/a.mp4"}, "model": video.SEEDANCE_MODEL}
    downloads = []
    async def download(url, target):
        downloads.append(url)
        if len(downloads) == 1: raise OSError("offline interrupted download")
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b"offline ark media")
    monkeypatch.setattr(video, "_post_json", post); monkeypatch.setattr(video, "_get_json", get)
    monkeypatch.setattr(runtime, "_download", download)
    first = run(runtime.execute_job(value, project, tmp_path))
    assert first["status"] == "queued" and first["result"]["provider"] == "seedance"
    settings.seedance_base_url, settings.seedance_model = "https://changed.example", "changed-model"
    monkeypatch.setenv("TASK_VIDEO_SCENE_PROVIDER", "runninghub")
    second = run(runtime.execute_job({**value, "provider_task_id": "ark-pinned-1", "result": first["result"]}, project, tmp_path))
    assert second["status"] == "succeeded" and second["result"]["provider"] == "seedance"
    assert [c[0] for c in calls] == ["post", "get", "get"]
    assert all(c[1].startswith(video.SEEDANCE_BASE_URL) for c in calls)
    assert calls[0][2] == video.SEEDANCE_MODEL


@pytest.mark.parametrize("change", ["endpoint", "model"])
def test_new_seedance_jobs_refuse_nonofficial_configuration_without_fallback(monkeypatch, project, change):
    _, settings, _ = seedance_direct(monkeypatch, project)
    if change == "endpoint": settings.seedance_base_url = "https://proxy.example.test"
    else: settings.seedance_model = "other-model"
    assert runtime.runtime_capabilities()["scene"]["ready"] is False
    with pytest.raises(HTTPException, match="不能自动切换"):
        runtime.prepare_job({"kind": "scene", "scene_id": "s1"}, project)


def test_known_runninghub_receipt_uses_original_provider_with_seedance_default(monkeypatch, tmp_path, project):
    from app import video_runninghub as rh
    seedance_direct(monkeypatch, project)
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "synthetic-rh-key")
    calls = []
    async def query(identifier):
        calls.append(identifier); return {"status": "running"}
    monkeypatch.setattr(rh, "query_scene", query)
    monkeypatch.setattr(rh, "submit_scene", lambda *_: pytest.fail("Known RH receipt must never resubmit"))
    value = job(provider_task_id="original-rh-task", result={"provider": "runninghub"})
    result = run(runtime.execute_job(value, project, tmp_path))
    assert result["status"] == "running" and calls == ["original-rh-task"]
    assert result["result"]["provider"] == "runninghub"


def test_unidentified_or_ambiguous_historical_receipt_never_guesses_new_provider(monkeypatch, tmp_path, project):
    seedance_direct(monkeypatch, project)
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "synthetic-rh-key")
    with pytest.raises(HTTPException, match="缺少可靠的供应商"):
        run(runtime.execute_job(job(provider_task_id="unidentified-task", result={}), project, tmp_path))
    with pytest.raises(HTTPException, match="重新确认生成"):
        run(runtime.execute_job(job(submission_started=True, result={"provider": "runninghub"}), project, tmp_path))


def test_switched_runninghub_batch_does_not_submit_remaining_children(monkeypatch, tmp_path, project):
    _, _, value = runninghub_fixture(monkeypatch, project, "scene_batch")
    value["provider_task_id"] = "batch-ledger:existing"
    value["result"] = {"provider": "runninghub", "scene_ids": ["s1", "s2"], "scene_results": [
        {"scene_id": "s1", "status": "running", "provider_task_id": "existing-rh-child", "submission_started": True}]}
    monkeypatch.setenv("TASK_VIDEO_SCENE_PROVIDER", "seedance")
    calls = []
    async def child(request, current, root, callback):
        calls.append(request["provider_task_id"])
        target = root / "already-paid.mp4"; target.write_bytes(b"offline existing RH result")
        return {"status": "succeeded", "provider_task_id": request["provider_task_id"], "result": {"path": "already-paid.mp4", "provider": "runninghub"}}
    monkeypatch.setattr(runtime, "_scene_job", child)
    with pytest.raises(HTTPException, match="重新确认生成"):
        run(runtime.execute_job(value, project, tmp_path))
    assert calls == ["existing-rh-child"]


def test_ffmpeg_probe_is_cached_and_python_config_change_refreshes(monkeypatch, tmp_path):
    import builtins
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"offline ffmpeg marker")
    calls = []
    original = builtins.__import__
    def no_local_imageio(name, *args, **kwargs):
        if name == "imageio_ffmpeg":
            raise ImportError("test forces configured runtime probe")
        return original(name, *args, **kwargs)
    def probe(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=str(binary))
    monkeypatch.setattr(builtins, "__import__", no_local_imageio)
    monkeypatch.setattr(runtime.shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime.subprocess, "run", probe)
    runtime._discover_ffmpeg.cache_clear()
    assert runtime._discover_ffmpeg("", "python-one", "test-path") == str(binary)
    assert runtime._discover_ffmpeg("", "python-one", "test-path") == str(binary)
    assert len(calls) == 1
    assert runtime._discover_ffmpeg("", "python-two", "test-path") == str(binary)
    assert len(calls) == 2
    runtime._discover_ffmpeg.cache_clear()


@pytest.mark.parametrize("kind", ["scene", "voice", "avatar"])
def test_unknown_price_blocks_paid_submission(monkeypatch, project, kind):
    ready(monkeypatch)
    for name in ("SCENE_COST_PER_SECOND", "VOICE_COST_PER_1000_CHARS", "AVATAR_COST_PER_SECOND"):
        monkeypatch.delenv("TASK_VIDEO_" + name)
    assert runtime.estimate_job(job(kind), project)["amount"] is None
    with pytest.raises(HTTPException) as error:
        runtime.validate_job(job(kind), project)
    assert error.value.status_code == 503


@pytest.mark.parametrize("change", ["no_grant", "old_revision", "too_small", "wrong_currency", "nan"])
def test_budget_is_a_versioned_upper_bound(monkeypatch, project, change):
    ready(monkeypatch)
    value = job()
    if change == "no_grant":
        value.pop("budget_grant")
    elif change == "old_revision":
        value["budget_grant"]["revision"] = 0
    elif change == "too_small":
        value["budget_grant"]["max_cost"] = 0.1
    elif change == "wrong_currency":
        value["budget_grant"]["currency"] = "RH"
    else:
        value["budget_grant"]["max_cost"] = "NaN"
    with pytest.raises(HTTPException) as error:
        runtime.validate_job(value, project)
    assert error.value.status_code == 409


def test_changed_revision_and_blank_review_do_not_authorize(monkeypatch, project):
    ready(monkeypatch)
    project["reviews"]["generation"]["revision"] = 0
    with pytest.raises(HTTPException):
        runtime.validate_job(job(), project)
    project["reviews"]["generation"]["revision"] = 1
    project["reviews"]["generation"]["quote"] = "  "
    with pytest.raises(HTTPException):
        runtime.validate_job(job(), project)


def test_scene_id_is_persisted_and_recovery_only_queries(monkeypatch, tmp_path, project):
    video = provider(monkeypatch)
    ready(monkeypatch)
    calls, events = [], []
    async def submit(payload):
        calls.append("submit")
        assert events[-1]["status"] == "submitting"
        return {"id": "task-1"}
    async def query(task_id):
        assert task_id == "task-1"
        assert any(e.get("provider_task_id") == "task-1" for e in events)
        calls.append("query")
        return {"status": "queued"}
    monkeypatch.setattr(video, "_submit_generation", submit)
    monkeypatch.setattr(video, "_query_generation", query)
    first = run(runtime.execute_job(job(), project, tmp_path, events.append))
    second = run(runtime.execute_job(job(provider_task_id="task-1", budget_grant=None), project, tmp_path, events.append))
    assert first["status"] == second["status"] == "queued"
    assert calls == ["submit", "query", "query"]


def test_download_failure_never_resubmits(monkeypatch, tmp_path, project):
    video = provider(monkeypatch)
    ready(monkeypatch)
    async def query(task_id):
        return {"status": "succeeded", "videoUrl": "https://media.example.test/test.mp4"}
    async def no_submit(_):
        pytest.fail("Paid task was submitted again")
    attempts = []
    async def download(_url, destination):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("offline")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"offline-provider-video")
    monkeypatch.setattr(video, "_submit_generation", no_submit)
    monkeypatch.setattr(video, "_query_generation", query)
    monkeypatch.setattr(runtime, "_download", download)
    value = job(provider_task_id="task-finished", budget_grant=None)
    assert run(runtime.execute_job(value, project, tmp_path))["status"] == "queued"
    result = run(runtime.execute_job(value, project, tmp_path))
    assert result["status"] == "succeeded"
    assert (tmp_path / result["result"]["path"]).is_file()
    assert len(attempts) == 2


def fake_media_download(monkeypatch, addresses, redirects=None, failure=None):
    """Real download code and HTTP response handling, with no DNS/network I/O."""
    requests, dns, options = [], [], []
    def resolve(host, port, *args):
        dns.append((host, port))
        return [(0, 0, 0, "", (address, port)) for address in addresses.get(host, [])]
    def respond(request):
        requests.append(str(request.url))
        if failure:
            raise failure("synthetic transport failure", request=request)
        target = (redirects or {}).get(str(request.url))
        return runtime.httpx.Response(302, headers={"location": target}) if target else runtime.httpx.Response(
            200, content=b"offline-provider-video", headers={"content-type": "video/mp4"})
    original_client = runtime.httpx.AsyncClient
    def client(**kwargs):
        options.append(kwargs)
        # An allowlist exception must never disable normal TLS verification.
        assert kwargs.get("verify", True) is True
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original_client(transport=runtime.httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(runtime.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(runtime.httpx, "AsyncClient", client)
    return requests, dns, options


@pytest.mark.parametrize("url,addresses,allowed", [
    ("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.mp4", ["198.18.2.30"], True),
    ("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com:443/a.mp4", ["198.18.0.0", "198.19.255.255"], True),
    ("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.mp4", ["198.18.2.30", "8.8.8.8"], True),
    ("https://media.example.test/a.mp4", ["8.8.8.8"], True),
    ("https://media.example.test/a.mp4", ["198.18.2.30"], False),
    ("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com.evil.test/a.mp4", ["198.18.2.30"], False),
    ("https://sub.ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.mp4", ["198.18.2.30"], False),
    ("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com:444/a.mp4", ["198.18.2.30"], False),
    ("http://ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.mp4", ["198.18.2.30"], False),
    ("https://user:pass@ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.mp4", ["198.18.2.30"], False),
    ("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.mp4", [], False),
    *[("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.mp4", ["198.18.2.30", address], False)
      for address in ("10.0.0.1", "172.16.0.1", "192.168.1.1", "127.0.0.1", "169.254.1.1", "100.64.0.1",
                      "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:198.18.2.30")],
])
def test_download_fake_ip_exception_is_exact_and_rejects_untrusted_dns(monkeypatch, tmp_path, url, addresses, allowed):
    host = runtime.urlparse(url).hostname
    requests, _, options = fake_media_download(monkeypatch, {host: addresses})
    target = tmp_path / "scene.mp4"
    if allowed:
        run(runtime._download(url, target))
        assert target.read_bytes() == b"offline-provider-video" and len(requests) == 1
    else:
        with pytest.raises(RuntimeError):
            run(runtime._download(url, target))
        assert requests == [] and not target.exists() and not target.with_suffix(".mp4.part").exists()
    assert options and options[0]["timeout"] == 120


@pytest.mark.parametrize("redirect,allowed", [
    ("https://media.example.test/next.mp4", False),
    ("https://127.0.0.1/next.mp4", False),
    ("http://ark-acg-cn-beijing.tos-cn-beijing.volces.com/next.mp4", False),
    ("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com:444/next.mp4", False),
    ("https://ark-acg-cn-beijing.tos-cn-beijing.volces.com:443/next.mp4", True),
])
def test_download_rechecks_fake_ip_exception_on_every_redirect(monkeypatch, tmp_path, redirect, allowed):
    source = "https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.mp4"
    requests, dns, _ = fake_media_download(monkeypatch,
        {runtime.SEEDANCE_MEDIA_HOST: ["198.18.2.30"], "media.example.test": ["198.18.2.31"], "127.0.0.1": ["127.0.0.1"]},
        {source: redirect})
    target = tmp_path / "scene.mp4"
    if allowed:
        run(runtime._download(source, target))
        assert len(requests) == 2 and len(dns) == 2 and target.is_file()
    else:
        with pytest.raises(RuntimeError):
            run(runtime._download(source, target))
        assert requests == [source] and not target.exists()


def test_download_fake_ip_does_not_suppress_tls_failure(monkeypatch, tmp_path):
    requests, _, _ = fake_media_download(monkeypatch, {runtime.SEEDANCE_MEDIA_HOST: ["198.18.2.30"]},
        failure=runtime.httpx.ConnectError)
    with pytest.raises(runtime.httpx.ConnectError):
        run(runtime._download(f"https://{runtime.SEEDANCE_MEDIA_HOST}/a.mp4", tmp_path / "scene.mp4"))
    assert len(requests) == 1 and not (tmp_path / "scene.mp4").exists()


def test_seedance_completed_id_fake_ip_recovery_queries_and_downloads_without_submit(monkeypatch, tmp_path, project):
    video, _, value = seedance_direct(monkeypatch, project)
    calls = []
    async def forbidden(*args, **kwargs):
        pytest.fail("An existing completed provider task must never be submitted again")
    async def query(task_id, **kwargs):
        calls.append(task_id)
        return {"status": "succeeded", "model": runtime.SEEDANCE_MODEL,
                "videoUrl": f"https://{runtime.SEEDANCE_MEDIA_HOST}/completed.mp4"}
    monkeypatch.setattr(video, "_submit_generation", forbidden)
    monkeypatch.setattr(video, "_query_generation", query)
    requests, _, _ = fake_media_download(monkeypatch, {runtime.SEEDANCE_MEDIA_HOST: ["198.18.2.30"]})
    value.update(provider_task_id="original-finished-id", submission_started=True, status="running")
    result = run(runtime.execute_job(value, project, tmp_path))
    assert result["status"] == "succeeded" and result["provider_task_id"] == "original-finished-id"
    assert calls == ["original-finished-id"] and len(requests) == 1
    assert (tmp_path / result["result"]["path"]).read_bytes() == b"offline-provider-video"


def test_ambiguous_submission_is_blocked(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    result = run(runtime.execute_job(job(status="submitting"), project, tmp_path))
    assert result["status"] == "blocked"
    assert "不能自动重复" in result["error"]


def test_provider_errors_are_sanitized_and_queries_preserve_receipt(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    video = provider(monkeypatch)
    async def failure(*args):
        raise HTTPException(502, "must-not-leak-provider-secret")
    monkeypatch.setattr(video, "_submit_generation", failure)
    monkeypatch.setattr(video, "_query_generation", failure)
    submit_result = run(runtime.execute_job(job(), project, tmp_path))
    query_result = run(runtime.execute_job(job(provider_task_id="existing-task"), project, tmp_path))
    assert submit_result["status"] == "blocked"
    assert query_result["status"] == "queued"
    assert query_result["provider_task_id"] == "existing-task"
    assert "must-not-leak" not in json.dumps([submit_result, query_result])


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(HTTPException):
        runtime._safe_path(tmp_path, "../credential", exists=False)


def test_review_bridge_uses_exact_user_quote(monkeypatch, tmp_path, project):
    calls = []
    async def cli(_root, script, *args, **kwargs):
        calls.append((script, args))
        return {}
    monkeypatch.setattr(runtime, "_cli", cli)
    project["reviews"]["voice"].update(decision="skipped", quote="不用先试听，直接生成")
    run(runtime._map_review(project, tmp_path, "voice"))
    assert calls[0][1][:2] == ("present", "voice")
    assert calls[1][1][:2] == ("skip", "voice")
    assert calls[1][1][-1] == "不用先试听，直接生成"
    assert len(calls) == 2


def make_wav(path, seconds=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * int(seconds * 16000))


def test_voice_invokes_bundle_pipeline_and_requires_real_outputs(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    calls = []
    async def cli(root, script, *args, **kwargs):
        calls.append((script, args))
        if script == "pipeline.py":
            make_wav(root / "out/merged.wav")
            (root / "out/words.json").write_text('{"words": []}', encoding="utf-8")
        return {}
    monkeypatch.setattr(runtime, "_cli", cli)
    result = run(runtime.execute_job(job("voice"), project, tmp_path))
    assert result["status"] == "succeeded"
    assert result["result"]["duration"] == 2
    assert any(c[0] == "pipeline.py" and c[1][0] == "voice" and "--yes" in c[1] for c in calls)
    assert (tmp_path / result["result"]["audio_path"]).is_file()


def test_real_review_ledger_invalidates_after_content_change(monkeypatch, tmp_path, project):
    monkeypatch.setattr(runtime, "_python", lambda: sys.executable)
    (tmp_path / "copy.txt").write_text("Offline user-approved copy.", encoding="utf-8")
    run(runtime._cli(tmp_path, "review.py", "init", "--workdir", tmp_path))
    run(runtime._map_review(project, tmp_path, "inputs"))
    ledger = json.loads((tmp_path / "review_hooks.json").read_text(encoding="utf-8"))
    assert ledger["stages"]["inputs"]["quote"] == project["reviews"]["inputs"]["quote"]
    (tmp_path / "copy.txt").write_text("Changed copy.", encoding="utf-8")
    result = run(runtime._cli(tmp_path, "review.py", "status", "--workdir", tmp_path))
    assert result["reviews"]["inputs"]["state"] == "stale"


def find_test_ffmpeg():
    explicit = os.environ.get("TEST_VIDEO_FFMPEG", "")
    if explicit and Path(explicit).is_file():
        return explicit
    # The test runner must explicitly provide a binary if its environment has none.
    import shutil
    return shutil.which("ffmpeg") or ""


@pytest.mark.parametrize("mode", ["standard", "avatar"])
def test_real_ffmpeg_render_produces_three_truthful_platform_packages(monkeypatch, tmp_path, project, mode):
    ffmpeg = find_test_ffmpeg()
    if not ffmpeg:
        pytest.skip("Set TEST_VIDEO_FFMPEG for actual local encoding verification")
    ready(monkeypatch)
    monkeypatch.setattr(runtime, "_ffmpeg", lambda: ffmpeg)
    make_wav(tmp_path / "voice.wav", 2)
    # Real two-colour provider fixtures; this process has no external network calls.
    for index, color in enumerate(("blue", "green"), 1):
        path = tmp_path / f"scene{index}.mp4"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=180x320:d=1:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
        project["jobs"].append({"id": f"source{index}", "kind": "scene", "scene_id": f"s{index}", "status": "succeeded", "revision": 1, "result": {"path": path.name}})
    project["jobs"].append({"id": "v1", "kind": "voice", "status": "succeeded", "revision": 1, "result": {"audio_path": "voice.wav"}})
    if mode == "avatar":
        project["mode"] = mode
        # A shorter finalized avatar proves the renderer keeps the synchronized
        # avatar soundtrack, rather than replacing it with longer original TTS.
        avatar_path = tmp_path / "avatar-final.mp4"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp_path / "scene1.mp4"),
                        "-i", str(tmp_path / "voice.wav"), "-t", "1", "-c:v", "copy", "-c:a", "aac", str(avatar_path)], check=True)
        project["jobs"].append({"id": "a1", "kind": "avatar", "status": "succeeded", "revision": 1, "result": {"path": avatar_path.name}})
    result = run(runtime.execute_job(job("render"), project, tmp_path))
    assert result["status"] == "succeeded", result
    exports = result["result"]["exports"]
    assert {e["platform"] for e in exports} == {"weixin", "douyin", "xiaohongshu"}
    for entry in exports:
        assert (tmp_path / entry["path"]).stat().st_size > 1000
        manifest = json.loads((tmp_path / entry["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["same_edit_as_master"] is True
        assert manifest["status"] == "awaiting_video_review"
        assert manifest["subtitles_burned"] is (mode == "standard")
    media = tmp_path / result["result"]["path"]
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(media)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "720x1280" in probe.stderr
    assert ("Duration: 00:00:02.00" if mode == "standard" else "Duration: 00:00:01.") in probe.stderr
    assert "Audio: aac" in probe.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows account-scoped render path regression")
def test_real_ffmpeg_render_long_windows_root_exports_every_platform_without_touching_history(monkeypatch, tmp_path, project):
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
        if winreg.QueryValueEx(key, "LongPathsEnabled")[0]:
            pytest.skip("The old-path failure control requires Windows long paths to be disabled")
    ffmpeg = find_test_ffmpeg()
    if not ffmpeg:
        pytest.skip("Set TEST_VIDEO_FFMPEG for actual local encoding verification")
    padding = 187 - len(str(tmp_path.resolve()))
    assert padding > 0, "Use a short temporary base for the Windows path regression"
    root = tmp_path.resolve() / ("r" * padding)
    root.mkdir()
    assert len(str(root)) == 188
    ready(monkeypatch)
    monkeypatch.setattr(runtime, "_ffmpeg", lambda: ffmpeg)
    async def no_cli(*args, **kwargs):
        pytest.fail("Local render must reuse the existing voice and scenes")
    def no_cloud(*args, **kwargs):
        pytest.fail("Local render must not create an HTTP client or a provider request")
    monkeypatch.setattr(runtime, "_cli", no_cli)
    monkeypatch.setattr(runtime.httpx, "AsyncClient", no_cloud)
    make_wav(root / "voice.wav", 2)
    for index, color in enumerate(("blue", "green"), 1):
        path = root / f"scene{index}.mp4"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                        f"color=c={color}:s=180x320:d=1:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
        project["jobs"].append({"id": f"source{index}", "kind": "scene", "scene_id": f"s{index}",
                                "status": "succeeded", "revision": 1, "result": {"path": path.name}})
    project["jobs"].append({"id": "existing-voice", "kind": "voice", "status": "succeeded", "revision": 1,
                            "result": {"audio_path": "voice.wav"}})
    identifier = "8df1ed7d-16cc-49ab-9527-c9fc4e021c6c"
    legacy = root / "renders" / identifier
    legacy.mkdir(parents=True)
    (legacy / "master.mp4").write_bytes(b"historical-media-must-remain-unchanged")
    (legacy / "history.txt").write_text("existing render history", encoding="utf-8")
    old_manifest = legacy / "xiaohongshu/manifest.json"
    assert len(str(old_manifest.with_suffix(".json.tmp"))) == 263
    with pytest.raises(OSError):
        runtime._dump(old_manifest, {"platform": "xiaohongshu", "offline_failure_control": True})
    assert not old_manifest.exists() and not old_manifest.with_suffix(".json.tmp").exists()
    old_bytes = {path: path.read_bytes() for path in legacy.rglob("*") if path.is_file()}
    source_bytes = {path: path.read_bytes() for path in (root / "voice.wav", root / "scene1.mp4", root / "scene2.mp4")}
    dump_paths = []
    original_dump = runtime._dump
    def checked_dump(path, value):
        temporary = path.with_suffix(path.suffix + ".tmp")
        assert len(str(path.resolve())) < 260 and len(str(temporary.resolve())) < 260
        dump_paths.append((path, temporary))
        return original_dump(path, value)
    monkeypatch.setattr(runtime, "_dump", checked_dump)
    outcome = run(runtime.execute_job(job("render", id=identifier), project, root))
    assert outcome["status"] == "succeeded", outcome
    destination = root / "renders" / hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:24]
    assert root / outcome["result"]["path"] == destination / "master.mp4"
    exports = outcome["result"]["exports"]
    assert {entry["platform"] for entry in exports} == {"weixin", "douyin", "xiaohongshu"}
    for entry in exports:
        for key in ("path", "cover_path", "subtitle_path", "manifest_path", "publication_path"):
            path = root / entry[key]
            assert path.is_file() and path.read_bytes() and len(str(path.resolve())) < 260
        assert json.loads((root / entry["manifest_path"]).read_text(encoding="utf-8"))["platform"] == entry["platform"]
        assert "发布说明" in (root / entry["publication_path"]).read_text(encoding="utf-8")
    assert len(dump_paths) == 3 and max(len(str(temporary.resolve())) for _, temporary in dump_paths) == 251
    assert all(len(str(path.resolve())) < 260 for path in destination.rglob("*") if path.is_file())
    assert {path: path.read_bytes() for path in legacy.rglob("*") if path.is_file()} == old_bytes
    assert {path: path.read_bytes() for path in source_bytes} == source_bytes
    assert runtime._safe_path(root, runtime._relative(root, legacy / "master.mp4")).read_bytes() == old_bytes[legacy / "master.mp4"]


def test_render_hash_uses_complete_legal_long_id_and_keeps_common_prefix_jobs_separate(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    make_wav(tmp_path / "voice.wav", 2)
    (tmp_path / "scene.mp4").write_bytes(b"existing-scene-fixture")
    project["jobs"].append({"id": "existing-voice", "kind": "voice", "status": "succeeded", "revision": 1,
                            "result": {"audio_path": "voice.wav"}})
    for scene in project["plan"]["scenes"]:
        project["jobs"].append({"id": "source-" + scene["id"], "kind": "scene", "scene_id": scene["id"],
                                "status": "succeeded", "revision": 1, "result": {"path": "scene.mp4"}})
    async def local_ffmpeg(*args):
        if args[-1] != "-":
            Path(args[-1]).write_bytes(b"offline-local-render-output")
    async def no_cli(*args, **kwargs):
        pytest.fail("Render must not regenerate existing provider media")
    def no_cloud(*args, **kwargs):
        pytest.fail("Render must remain entirely local")
    monkeypatch.setattr(runtime, "_ffrun", local_ffmpeg)
    monkeypatch.setattr(runtime, "_cli", no_cli)
    monkeypatch.setattr(runtime.httpx, "AsyncClient", no_cloud)
    prefix = "custom-" + "a" * 92
    paths = []
    for suffix in ("A", "B"):
        identifier = prefix + suffix
        assert len(identifier) == 100 and runtime._identifier(identifier) == identifier
        outcome = run(runtime.execute_job(job("render", id=identifier), project, tmp_path))
        assert outcome["status"] == "succeeded", outcome
        expected = "renders/" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:24] + "/master.mp4"
        assert outcome["result"]["path"] == expected and (tmp_path / expected).is_file()
        paths.append(expected)
    assert paths[0] != paths[1] and all((tmp_path / path).is_file() for path in paths)


def test_avatar_requires_picture_without_spending(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    project["mode"] = "avatar"
    async def no_cli(*args, **kwargs):
        pytest.fail("No CLI should run without the required picture")
    monkeypatch.setattr(runtime, "_cli", no_cli)
    with pytest.raises(HTTPException) as error:
        run(runtime._prepare_voice(project, tmp_path))
    assert error.value.status_code == 409


def test_resume_avatar_returns_existing_provider_task(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    project["mode"] = "avatar"
    wd = runtime._skill_workdir(tmp_path, project)
    make_wav(wd / "out/merged.wav")
    runtime._dump(wd / "avatar/result.json", {"pending": {"1": {"task": "rh-existing"}}, "results": {}})
    events = []
    async def cli(root, script, *args, **kwargs):
        assert script == "avatar/run_avatar.py"
        assert "--only" not in args and "--all" not in args
        await kwargs["watch"]()
        raise RuntimeError("still running")
    monkeypatch.setattr(runtime, "_cli", cli)
    result = run(runtime._avatar_job(job("avatar", provider_task_id='{"1":"rh-existing"}'), project, tmp_path, events.append))
    assert result["status"] == "running"
    assert json.loads(result["provider_task_id"]) == {"1": "rh-existing"}
    assert any(json.loads(e["provider_task_id"])["1"] == "rh-existing" for e in events if e.get("provider_task_id"))


def test_avatar_receipt_recovers_crash_before_normal_state_save(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    project["mode"] = "avatar"
    wd = runtime._skill_workdir(tmp_path, project)
    make_wav(wd / "out/merged.wav")
    runtime._dump(wd / "avatar/submission_1.json", {"segment": 1, "status": "submitted", "task": "rh-crash-receipt"})
    async def cli(root, script, *args, **kwargs):
        assert script == "avatar/run_avatar.py"
        saved = json.loads((root / "avatar/result.json").read_text(encoding="utf-8"))
        assert saved["pending"]["1"]["task"] == "rh-crash-receipt"
        raise RuntimeError("still running")
    monkeypatch.setattr(runtime, "_cli", cli)
    result = run(runtime._avatar_job(job("avatar", status="submitting"), project, tmp_path, None))
    assert result["status"] == "running"
    assert json.loads(result["provider_task_id"]) == {"1": "rh-crash-receipt"}


def test_avatar_full_adapter_sequence_is_not_a_fake_media_success(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    project["mode"] = "avatar"
    project["plan"]["avatar"] = {"image_type": "D", "image_use_confirmed": True, "hands": "双手自然垂在身体两侧。", "posture": "身体站直。",
                                  "motion": "从<Picture 1>的姿态开始，抬手示意，最后回到<Picture 1>的姿态。", "mouth_box": [0.4, 0.2, 0.6, 0.3]}
    wd = runtime._skill_workdir(tmp_path, project)
    make_wav(wd / "out/merged.wav", 2)
    (wd / "assets").mkdir()
    (wd / "assets/presenter.png").write_bytes(b"offline simulated portrait")
    calls = []
    async def cli(root, script, *args, **kwargs):
        calls.append((script, args))
        if script == "pipeline.py" and args[0] == "segments":
            runtime._dump(root / "avatar/plan.json", {"total_seconds": 2, "audio_sha": runtime._audio_sha(root / "out/merged.wav"),
                "segments": [{"i": 1, "text_asr": "测试台词", "start": 0, "end": 2, "duration": 2}]})
        return {}
    monkeypatch.setattr(runtime, "_cli", cli)
    run(cli(wd, "pipeline.py", "segments"))
    project["jobs"].append({"id": "voice1", "kind": "voice", "revision": 1, "status": "succeeded", "result": {"avatar_preflight": runtime._capture_avatar_preflight(wd, tmp_path)}})
    calls.clear()
    with pytest.raises(RuntimeError, match="未产出真实视频"):
        run(runtime._avatar_job(job("avatar"), project, tmp_path, None))
    assert [item[0] for item in calls] == ["avatar/prepare_image.py", "pipeline.py", "avatar/build_prompts.py",
        "review.py", "review.py", "review.py", "review.py", "review.py", "review.py", "avatar/run_avatar.py", "pipeline.py"]
    assert "--retries" in calls[-2][1] and "0" in calls[-2][1]
    assert "--mouth-box" in calls[0][1]
    assert calls[0][1][-1] == "0.4,0.2,0.6,0.3"
    for item in calls:
        if item[0] == "review.py" and item[1][0] == "approve":
            stage = item[1][1]
            assert item[1][-1] == project["reviews"][stage]["quote"]


def test_scene_boundaries_follow_matching_spoken_words():
    scenes = [{"duration": 4, "narration": "先看问题。"}, {"duration": 4, "narration": "再看解释。"}]
    words = [{"text": "先看问题", "start": 0, "end": 1}, {"text": "再看解释", "start": 1, "end": 4}]
    lengths, basis = runtime._aligned_lengths(scenes, 4, words)
    assert lengths == [1, 3]
    assert "逐词" in basis


def test_missing_words_falls_back_with_explicit_timing_disclosure():
    lengths, basis = runtime._aligned_lengths([{"duration": 4}, {"duration": 8}], 6, [])
    assert lengths == [2, 4]
    assert "按方案时长比例" in basis


def test_hidden_default_avatar_actions_cannot_be_approved_by_generic_confirmation(monkeypatch, project):
    ready(monkeypatch)
    project["mode"] = "avatar"
    project["plan"]["avatar"] = {"image_use_confirmed": True}
    # Even genuine generic stage approval cannot approve a motion that was absent
    # from the displayed plan.
    with pytest.raises(HTTPException, match="隐藏默认动作"):
        runtime.validate_job(job("voice"), project)


def test_avatar_portrait_use_requires_explicit_authorization(monkeypatch, project):
    ready(monkeypatch)
    project["mode"] = "avatar"
    project["plan"]["avatar"] = {"image_type": "D", "hands": "已规划", "posture": "已规划", "motion": "已规划", "image_use_confirmed": False}
    with pytest.raises(HTTPException, match="使用授权"):
        runtime.validate_job(job("voice"), project)


def test_receipt_recovery_is_read_only_and_does_not_submit(tmp_path, project, monkeypatch):
    wd = runtime._skill_workdir(tmp_path, project)
    receipt = wd / "avatar/submission_1.json"
    runtime._dump(receipt, {"segment": 1, "task": "rh-persisted-task", "status": "submitted"})
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    recovered = runtime.recover_receipt(job("avatar"), project, tmp_path)
    assert json.loads(recovered) == {"1": "rh-persisted-task"}
    after = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after


def measured_voice(project, seconds=20):
    durations = [14, seconds - 14] if seconds > 14 else [seconds]
    cursor, segments = 0, []
    for index, duration in enumerate(durations, 1):
        segments.append({"index": index, "start": cursor, "end": cursor + duration, "duration": duration})
        cursor += duration
    project["jobs"].append({"id": "v-measured", "kind": "voice", "revision": project["revision"], "status": "succeeded", "result": {
        "avatar_preflight": {"scope": "project", "duration_source": "actual_voice", "total_seconds": seconds,
                             "segment_count": len(segments), "segments": segments}}})


def official_profile(monkeypatch):
    monkeypatch.setenv("TASK_VIDEO_PRICING_PROFILE", "official-cn-20261022")
    monkeypatch.setattr(runtime, "_settings", lambda: SimpleNamespace(seedance_model="doubao-seedance-2-0-mini-260615"))


def test_official_profile_prices_both_resolutions_without_legacy_manual_prices(monkeypatch, project):
    official_profile(monkeypatch)
    project["plan"]["scenes"][0]["duration"] = 6
    quote = runtime.estimate_job(job(), project)
    assert quote["amount"] == 2.9808 and quote["reservation_amount"] == 4.1732
    assert quote["provider_hard_cap"] is False and quote["pricing_kind"] == "estimate"
    assert quote["source_url"].startswith("https://docs.volcengine.com/")
    project["plan"]["resolution"] = "480p"
    assert runtime.estimate_job(job(), project)["amount"] == 1.3861
    value = job(budget_grant={"approved": True, "revision": 1, "max_cost": 1.4, "currency": "CNY"})
    with pytest.raises(HTTPException):
        runtime._budget(value, project)


def test_official_voice_reservation_is_not_an_official_character_price(monkeypatch, project):
    official_profile(monkeypatch)
    quote = runtime.estimate_job(job("voice"), project)
    assert quote["amount"] == quote["reservation_amount"] == 1
    assert quote["pricing_kind"] == "reservation" and quote["provider_hard_cap"] is False
    assert "不是官方按字定价" in quote["basis"]
    assert "1.5" in quote["basis"] and "12" in quote["basis"]


def test_official_avatar_reservation_uses_gpu_runtime_and_whole_segment_count(monkeypatch, project):
    official_profile(monkeypatch)
    measured_voice(project, 20)
    monkeypatch.setenv("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "SHARED")
    quote = runtime.estimate_job(job("avatar"), project)
    assert quote["currency"] == "CNY" and quote["amount"] == 2
    assert quote["gpu_hourly_cny"] == 6 and quote["segment_count"] == 2
    assert quote["runtime_reservation_seconds_per_segment"] == 600
    assert quote["duration_seconds"] == 20 and quote["provider_hard_cap"] is False
    monkeypatch.setenv("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "CONSUMER")
    assert runtime.estimate_job(job("avatar"), project)["amount"] is None


def test_official_price_is_not_applied_to_another_model(monkeypatch, project):
    official_profile(monkeypatch)
    monkeypatch.setattr(runtime, "_settings", lambda: SimpleNamespace(seedance_model="another-model"))
    assert runtime.estimate_job(job(), project)["amount"] is None
    monkeypatch.setattr(runtime, "_settings", lambda: SimpleNamespace(seedance_model="doubao-seedance-2-0-mini-260615", seedance_base_url="https://proxy.example.test"))
    assert runtime.estimate_job(job(), project)["amount"] is None


def test_voice_ledger_reconciles_last_event_and_keeps_unknowns(tmp_path):
    path = tmp_path / "usage.jsonl"
    rows = [{"call_id": "one", "state": "unknown", "cost_cny": None},
            {"call_id": "one", "state": "measured", "cost_cny": "0.012"},
            {"call_id": "two", "state": "unknown", "cost_cny": None}]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    value = runtime._voice_billing(path)
    assert value["known_cost"] == .012 and value["complete"] is False and value["unknown_requests"] == 1
    assert runtime._voice_billing(tmp_path / "missing")["complete"] is False


def test_scene_batch_stops_new_submissions_after_actual_usage_exceeds_reserve(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    official_profile(monkeypatch)
    calls = []
    async def child(value, _project, root, callback):
        calls.append(value["scene_id"])
        path = root / "first.mp4"
        path.write_bytes(b"offline")
        return {"status": "succeeded", "provider_task_id": "task-paid", "submission_started": True,
                "result": {"path": "first.mp4", "billing": {"currency": "CNY", "known_cost": 20, "complete": True}}}
    monkeypatch.setattr(runtime, "_scene_job", child)
    request = job("scene_batch", scene_ids=["s1", "s2"], budget_grant={"approved": True, "revision": 1, "max_cost": 10, "currency": "CNY"})
    outcome = run(runtime._scene_batch_job(request, project, tmp_path, None))
    assert calls == ["s1"] and outcome["status"] == "blocked"
    assert outcome["result"]["billing"]["known_cost"] == 20


def test_hermes_subprocess_disables_hidden_paid_retries(tmp_path):
    environment = runtime._child_env(tmp_path)
    assert environment["OLDX_PAID_MAX_TRIES"] == "1"
    assert environment["OLDX_VOICE_USAGE_LEDGER"] == str(tmp_path / "voice-usage.jsonl")


def runninghub_fixture(monkeypatch, project, kind="scene"):
    from app import video_runninghub as rh
    ready(monkeypatch)
    monkeypatch.setenv("TASK_VIDEO_SCENE_PROVIDER", "runninghub")
    monkeypatch.setenv("TASK_VIDEO_PAID_ENABLED", "1")
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "synthetic-runninghub-test-key")
    monkeypatch.setenv("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "SHARED")
    monkeypatch.setenv("TASK_VIDEO_PRICING_PROFILE", "official-cn-20261022")
    def quote(spec):
        amount = int(spec["body"]["duration"]) / 10
        return {"amount": amount, "reservation_amount": round(amount * 1.2, 4), "currency": "USD",
                "pricing_source": "runninghub_price_preview", "pricing_kind": "estimate", "source_url": rh.SOURCE_URL,
                "verified_at": "2026-10-22", "provider_hard_cap": False,
                "quoted_at": "2026-10-22T00:00:00+00:00", "expires_at": "2026-10-22T00:05:00+00:00"}
    monkeypatch.setattr(rh, "quote_scene", quote)
    request = {"kind": kind, "scene_id": "s1", **({"scene_ids": ["s1", "s2"]} if kind == "scene_batch" else {})}
    quoted = runtime.estimate_job(request, project)
    assert quoted["amount"] is not None
    value = job(kind, execution=quoted["execution"], estimated_cost=quoted,
                budget_grant={"approved": True, "revision": 1, "max_cost": 10, "currency": "USD", "estimated_cost": quoted},
                **({"scene_ids": ["s1", "s2"]} if kind == "scene_batch" else {}))
    return rh, quoted, value


def test_runninghub_quote_preserves_real_currency_and_pins_each_request(monkeypatch, project):
    _, quote, value = runninghub_fixture(monkeypatch, project, "scene_batch")
    assert quote["provider"] == "runninghub" and quote["currency"] == "USD"
    assert quote["amount"] == .8 and quote["reservation_amount"] == .96
    assert quote["execution"]["currency"] == "USD" and set(quote["execution"]["scenes"]) == {"s1", "s2"}
    assert quote["execution"]["scenes"]["s1"]["body"]["generateAudio"] is False
    assert "未换算汇率" in quote["basis"]
    value["budget_grant"]["currency"] = "CNY"
    with pytest.raises(HTTPException):
        runtime._budget(value, project)


def test_runninghub_scene_never_uses_ark_and_download_retry_only_queries(monkeypatch, tmp_path, project):
    rh, _, value = runninghub_fixture(monkeypatch, project)
    events, calls = [], []
    async def submit(spec):
        calls.append(("submit", spec["route"]))
        assert events[-1]["status"] == "submitting"
        return {"id": "rh-model-one", "status": "queued"}
    async def query(task):
        calls.append(("query", task))
        return {"status": "succeeded", "videoUrl": "https://media.example.test/rh.mp4", "currency": "USD", "usage": {"consumeMoney": "0.4"}}
    downloads = []
    async def download(_, destination):
        downloads.append(1)
        if len(downloads) == 1:
            raise OSError("offline transient download")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"offline RH provider fixture")
    monkeypatch.setattr(rh, "submit_scene", submit)
    monkeypatch.setattr(rh, "query_scene", query)
    monkeypatch.setattr(runtime, "_download", download)
    first = run(runtime.execute_job(value, project, tmp_path, events.append))
    assert first["status"] == "queued" and first["result"]["billing"]["known_cost"] == .4
    # A changed deployment default cannot redirect the original model task.
    monkeypatch.setenv("TASK_VIDEO_SCENE_PROVIDER", "seedance")
    second = run(runtime.execute_job({**value, "provider_task_id": "rh-model-one"}, project, tmp_path, events.append))
    assert second["status"] == "succeeded" and second["result"]["provider"] == "runninghub"
    assert second["result"]["billing"]["currency"] == "USD"
    assert [kind for kind, _ in calls] == ["submit", "query", "query"]


def test_pre_migration_receipts_still_poll_ark_without_runninghub_calls(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    monkeypatch.setenv("TASK_VIDEO_SCENE_PROVIDER", "runninghub")
    monkeypatch.setenv("TASK_VIDEO_PAID_ENABLED", "1")
    monkeypatch.setattr(runtime, "_settings", lambda: SimpleNamespace(seedance_api_key="offline-old-ark", seedance_model="old-ark"))
    video = provider(monkeypatch)
    called = []
    async def query(identifier):
        called.append(identifier)
        return {"status": "running"}
    monkeypatch.setattr(video, "_query_generation", query)
    result = run(runtime.execute_job(job(provider_task_id="old-ark-task", budget_grant=None), project, tmp_path))
    assert result["status"] == "running" and called == ["old-ark-task"]
    with pytest.raises(HTTPException, match="重新确认生成"):
        runtime.validate_job(job(), project)


def test_runninghub_ambiguous_post_never_repeats(monkeypatch, tmp_path, project):
    rh, _, value = runninghub_fixture(monkeypatch, project)
    calls = []
    async def submit(_):
        calls.append("submit")
        raise RuntimeError("offline ambiguous response")
    monkeypatch.setattr(rh, "submit_scene", submit)
    first = run(runtime.execute_job(value, project, tmp_path))
    second = run(runtime.execute_job({**value, "submission_started": True}, project, tmp_path))
    assert first["status"] == second["status"] == "blocked" and calls == ["submit"]


def test_runninghub_immediate_failure_preserves_bill_without_another_query(monkeypatch, tmp_path, project):
    rh, _, value = runninghub_fixture(monkeypatch, project)
    async def submit(_):
        return {"id": "rh-failed-one", "status": "failed", "currency": "USD", "usage": {"consumeMoney": ".2"}}
    async def no_query(_):
        pytest.fail("Immediate failure already supplied an authoritative receipt")
    monkeypatch.setattr(rh, "submit_scene", submit)
    monkeypatch.setattr(rh, "query_scene", no_query)
    result = run(runtime.execute_job(value, project, tmp_path))
    assert result["status"] == "failed" and result["provider_task_id"] == "rh-failed-one"
    assert result["result"]["billing"]["known_cost"] == .2


def test_runninghub_missing_or_different_bill_currency_is_not_guessed():
    unknown = runtime._runninghub_billing({"usage": {"consumeMoney": "3.2"}}, "USD")
    assert not unknown["complete"] and unknown["reported_amount"] == 3.2 and unknown["reported_currency"] == "UNKNOWN"
    assert unknown["known_cost"] == 0
    different = runtime._runninghub_billing({"currency": "CNY", "usage": {"consumeMoney": "3.2"}}, "USD")
    assert different["reported_currency"] == "CNY" and not different["complete"]
    mixed = runtime._sum_billing([{"currency": "CNY", "known_cost": 3, "complete": True}, {"currency": "USD", "known_cost": 1, "complete": True}])
    assert not mixed["complete"] and mixed["known_cost"] == 0


def test_runninghub_batch_stops_after_actual_spend_and_unknown_node_fee(monkeypatch, tmp_path, project):
    rh, _, value = runninghub_fixture(monkeypatch, project, "scene_batch")
    value["budget_grant"]["max_cost"] = 1
    calls = []
    async def child(request, _project, root, callback):
        calls.append(request["scene_id"])
        (root / "first.mp4").write_bytes(b"offline")
        return {"status": "succeeded", "provider_task_id": "rh-model-expensive", "submission_started": True,
                "result": {"path": "first.mp4", "billing": {"currency": "USD", "known_cost": .8, "complete": False, "source": "provider_report", "unknown_requests": 1}}}
    monkeypatch.setattr(runtime, "_scene_job", child)
    result = run(runtime._scene_batch_job(value, project, tmp_path, None))
    assert result["status"] == "blocked" and calls == ["s1"]
    assert result["result"]["billing"]["currency"] == "USD"


def direct_request(monkeypatch, project, kind="scene_batch"):
    ready(monkeypatch)
    monkeypatch.setenv("TASK_VIDEO_SCENE_PROVIDER", "runninghub")
    monkeypatch.setenv("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "SHARED")
    monkeypatch.setenv("TASK_VIDEO_COST_REVIEW_REQUIRED", "0")
    request = {"kind": kind, "scene_id": "s1", **({"scene_ids": ["s1", "s2"]} if kind == "scene_batch" else {})}
    intent = runtime.prepare_job(request, project)
    return job(kind, execution=intent.get("execution"), estimated_cost=intent,
               budget_grant={"approved": True, "revision": 1, "generation_consent": True},
               **({"scene_ids": ["s1", "s2"]} if kind == "scene_batch" else {}))


def test_direct_prepare_and_batch_never_query_prices(monkeypatch, tmp_path, project):
    from app import video_runninghub as rh
    def forbidden(*args):
        pytest.fail("Direct generation must not obtain a price or budget estimate")
    monkeypatch.setattr(rh, "quote_scene", forbidden)
    monkeypatch.setattr(runtime, "estimate_job", forbidden)
    value = direct_request(monkeypatch, project)
    calls = []
    async def child(request, _project, root, callback):
        identifier = request["scene_id"]
        calls.append(identifier)
        (root / f"{identifier}.mp4").write_bytes(b"offline direct fixture")
        return {"status": "succeeded", "provider_task_id": "direct-" + identifier, "submission_started": True,
                "result": {"path": f"{identifier}.mp4", "provider": "runninghub"}}
    monkeypatch.setattr(runtime, "_scene_job", child)
    result = run(runtime.execute_job(value, project, tmp_path))
    assert result["status"] == "succeeded" and calls == ["s1", "s2"]
    assert value["budget_grant"] == {"approved": True, "revision": 1, "generation_consent": True}


def test_direct_generation_still_requires_current_user_click(monkeypatch, project):
    value = direct_request(monkeypatch, project, "scene")
    value["budget_grant"].pop("generation_consent")
    with pytest.raises(HTTPException, match="用户点击生成"):
        runtime.validate_job(value, project)
    value["budget_grant"]["generation_consent"] = True
    value["budget_grant"]["revision"] = 0
    with pytest.raises(HTTPException, match="用户点击生成"):
        runtime.validate_job(value, project)


def test_direct_voice_job_has_no_budget_but_keeps_usage_ledger(monkeypatch, tmp_path, project):
    monkeypatch.setenv("TASK_VIDEO_COST_REVIEW_REQUIRED", "0")
    async def prepare(_project, _root):
        return tmp_path
    captured = {}
    async def cli(root, script, *args, **kwargs):
        captured.update(kwargs["paid_environment"])
        make_wav(root / "out/merged.wav")
        runtime._dump(root / "out/words.json", [])
    monkeypatch.setattr(runtime, "_prepare_voice", prepare)
    monkeypatch.setattr(runtime, "_cli", cli)
    result = run(runtime._voice_job(job("voice", budget_grant={"generation_consent": True}), project, tmp_path, None))
    assert result["status"] == "succeeded" and captured["OLDX_VOICE_BUDGET_CNY"] == ""
    assert captured["OLDX_VOICE_USAGE_LEDGER"].endswith("voice-usage-job-voice.jsonl")
    assert runtime._child_env(tmp_path)["OLDX_COST_REVIEW_REQUIRED"] == "0"


def test_scene_actual_cost_needs_the_submitted_or_returned_model(monkeypatch, tmp_path, project):
    video = provider(monkeypatch)
    official_profile(monkeypatch)
    async def query(_):
        return {"status": "succeeded", "videoUrl": "https://media.example.test/video", "usage": {"completion_tokens": 129600}}
    async def download(_, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"offline-provider-fixture")
    monkeypatch.setattr(video, "_query_generation", query)
    monkeypatch.setattr(runtime, "_download", download)
    unknown = run(runtime._scene_job(job(provider_task_id="historical-model-unknown"), project, tmp_path, None))
    assert unknown["result"]["billing"]["complete"] is False
    known = run(runtime._scene_job(job(provider_task_id="known-mini", result={"submission_spec": {
        "model": "doubao-seedance-2-0-mini-260615", "no_video_input": True}}), project, tmp_path, None))
    assert known["result"]["billing"]["known_cost"] == 2.9808 and known["result"]["billing"]["complete"] is True


def test_avatar_billing_with_pending_or_third_party_amount_stays_unreconciled(tmp_path):
    state = {"billing": {"task-one": {"task": "task-one", "budget_job_id": "avatar-current", "usage": {"consumeMoney": "0.2"}}},
             "pending": {"2": {"task": "task-two", "budget_job_id": "avatar-current"}}}
    runtime._dump(tmp_path / "avatar/result.json", state)
    report = runtime._avatar_billing_result(tmp_path, {"id": "avatar-current"})
    assert report["known_cost"] == .2 and report["complete"] is False and report["unknown_requests"] == 1
    state["pending"] = {}
    state["billing"]["task-one"]["usage"]["thirdPartyConsumeMoney"] = "0.3"
    runtime._dump(tmp_path / "avatar/result.json", state)
    report = runtime._avatar_billing_result(tmp_path, {"id": "avatar-current"})
    assert report["known_cost"] == .2 and report["third_party_reported_cny"] == .3 and report["complete"] is False


def test_new_avatar_authorization_does_not_resurrect_a_failed_receipt(monkeypatch, tmp_path, project):
    ready(monkeypatch)
    wd = runtime._skill_workdir(tmp_path, project)
    make_wav(wd / "out/merged.wav")
    runtime._dump(wd / "avatar/result.json", {"billing": {"failed-task": {"status": "FAILED"}}, "pending": {}, "results": {}})
    marker = wd / "avatar/submission_1.json"
    runtime._dump(marker, {"segment": 1, "task": "failed-task", "status": "submitted"})
    # The new job has no portrait approval and must reach that check, instead of
    # resurrecting the failed task and invoking the paid subprocess.
    with pytest.raises(HTTPException, match="使用授权"):
        run(runtime._avatar_job(job("avatar"), project, tmp_path, None))
    assert not marker.exists() and len(list((wd / "avatar/old").glob("*.json"))) == 1
    assert json.loads((wd / "avatar/result.json").read_text(encoding="utf-8"))["pending"] == {}


@pytest.mark.parametrize("key_type,currency", [("SHARED", "CNY"), ("CONSUMER", "RH")])
def test_avatar_quote_uses_key_currency_and_entire_measured_voice(monkeypatch, project, key_type, currency):
    ready(monkeypatch)
    measured_voice(project)
    monkeypatch.setenv("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", key_type)
    monkeypatch.setenv("TASK_VIDEO_AVATAR_CURRENCY", currency)
    monkeypatch.setenv("TASK_VIDEO_AVATAR_COST_PER_SECOND", "0.75")
    quote = runtime.estimate_job(job("avatar"), project)
    assert quote["amount"] == 15
    assert quote["currency"] == currency
    assert quote["scope"] == "project" and quote["scene_id"] is None
    assert quote["duration_seconds"] == 20 and quote["segment_count"] == 2
    assert quote["duration_source"] == "actual_voice"
    assert "输出秒数不代表 GPU" in quote["basis"]


def test_avatar_unknown_type_or_currency_conflict_never_quotes_a_fee(monkeypatch, project):
    ready(monkeypatch)
    measured_voice(project)
    assert runtime.estimate_job(job("avatar"), project)["amount"] is None
    monkeypatch.setenv("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "SHARED")
    monkeypatch.setenv("TASK_VIDEO_AVATAR_CURRENCY", "RH")
    quote = runtime.estimate_job(job("avatar"), project)
    assert quote["amount"] is None and quote["currency"] == "CNY"
    assert quote["billing_currency_confirmed"] is False


def test_avatar_planned_duration_never_substitutes_for_real_voice(monkeypatch, project):
    ready(monkeypatch)
    monkeypatch.setenv("TASK_VIDEO_RUNNINGHUB_KEY_TYPE", "SHARED")
    quote = runtime.estimate_job(job("avatar"), project)
    assert quote["amount"] is None
    assert "真实音频分段" in quote["basis"] or "缺少当前版本" in quote["basis"]


def test_preflight_requires_the_original_audio_fingerprint(tmp_path):
    wd = tmp_path / "skill"
    make_wav(wd / "out/merged.wav", 2)
    runtime._dump(wd / "avatar/plan.json", {"total_seconds": 2, "audio_sha": "not-the-original",
        "segments": [{"i": 1, "start": 0, "end": 2, "duration": 2}]})
    with pytest.raises(HTTPException, match="不匹配"):
        runtime._capture_avatar_preflight(wd, tmp_path)


def test_voice_runs_free_actual_avatar_segmentation_before_cost_confirmation(monkeypatch, tmp_path, project):
    project["mode"] = "avatar"
    wd = runtime._skill_workdir(tmp_path, project)
    calls = []
    async def prepare(*args):
        return wd
    async def cli(root, script, *args, **kwargs):
        calls.append(args[0])
        if args[0] == "voice":
            make_wav(wd / "out/merged.wav", 2)
            runtime._dump(wd / "out/words.json", {"words": []})
        elif args[0] == "segments":
            runtime._dump(wd / "avatar/plan.json", {"total_seconds": 2, "audio_sha": runtime._audio_sha(wd / "out/merged.wav"),
                "segments": [{"i": 1, "start": 0, "end": 2, "duration": 2}]})
    monkeypatch.setattr(runtime, "_prepare_voice", prepare)
    monkeypatch.setattr(runtime, "_cli", cli)
    result = run(runtime._voice_job(job("voice"), project, tmp_path, None))
    assert calls == ["voice", "segments"]
    assert result["result"]["avatar_preflight"]["total_seconds"] == 2
    assert result["result"]["avatar_preflight"]["segment_count"] == 1


def test_avatar_pause_trimming_requires_current_raw_job_review(monkeypatch, project):
    ready(monkeypatch)
    project["mode"] = "avatar"
    project["plan"]["postproduction"] = {"trim_pauses": True, "burn_subtitles": False}
    project["jobs"].append({"id": "a1", "kind": "avatar", "revision": 1, "status": "succeeded"})
    with pytest.raises(HTTPException, match="postprocess"):
        runtime.validate_job(job("render"), project)
    project["reviews"]["postprocess"] = {"revision": 1, "decision": "approved", "quote": "原片可以，开始去停顿", "job_id": "a1"}
    runtime.validate_job(job("render"), project)
    project["jobs"].append({"id": "a2", "kind": "avatar", "revision": 1, "status": "succeeded"})
    with pytest.raises(HTTPException, match="重新确认"):
        runtime.validate_job(job("render"), project)


def test_postproduction_defaults_do_not_add_avatar_subtitles(project):
    assert runtime._postproduction(project) == {"trim_pauses": False, "burn_subtitles": True}
    project["mode"] = "avatar"
    assert runtime._postproduction(project) == {"trim_pauses": False, "burn_subtitles": False}


def test_jumpcut_word_remap_uses_the_same_keep_intervals_as_picture():
    words = [{"text": "前", "start": .1, "end": .8}, {"text": "后", "start": 2.1, "end": 2.8}]
    mapped = runtime._remap_words(words, [{"src_start": 0, "src_end": 1, "out_start": 0}, {"src_start": 2, "src_end": 3, "out_start": 1}])
    assert mapped[1]["start"] == pytest.approx(1.1)
    assert mapped[1]["end"] == pytest.approx(1.8)
    assert words[1]["start"] == 2.1


def test_batch_quote_binds_only_explicit_missing_scene_ids(monkeypatch, project):
    ready(monkeypatch)
    value = job("scene_batch", scene_id=None, scene_ids=["s2"])
    quote = runtime.estimate_job(value, project)
    assert quote["amount"] == 2.4 and quote["scene_ids"] == ["s2"]
    assert quote["scope"] == "scene_batch" and quote["duration_seconds"] == 4
    value["scene_ids"] = ["s1", "s1"]
    assert runtime.estimate_job(value, project)["amount"] is None


def test_batch_persists_each_receipt_then_resumes_without_resubmission(monkeypatch, project, tmp_path):
    video = provider(monkeypatch)
    ready(monkeypatch)
    submitted, queries, events = [], [], []
    async def submit(payload):
        submitted.append(payload.prompt)
        return {"id": f"task-{len(submitted)}"}
    async def query(identifier):
        queries.append(identifier)
        if len(queries) == 1:
            return {"status": "running"}
        return {"status": "succeeded", "videoUrl": "https://example.test/fixture.mp4"}
    async def download(url, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"offline-provider-fixture")
    monkeypatch.setattr(video, "_submit_generation", submit)
    monkeypatch.setattr(video, "_query_generation", query)
    monkeypatch.setattr(runtime, "_download", download)
    value = job("scene_batch", scene_id=None, scene_ids=["s1", "s2"])
    first = run(runtime.execute_job(value, project, tmp_path, events.append))
    assert first["status"] == "running" and submitted == ["mock scene 1"]
    assert first["result"]["scene_results"][0]["provider_task_id"] == "task-1"
    assert runtime.recover_receipt(value, project, tmp_path) == "batch-ledger:job-scene_batch"
    # A crash may leave no in-memory result; the atomic disk ledger is sufficient.
    second = run(runtime.execute_job({**value, "provider_task_id": first["provider_task_id"]}, project, tmp_path, events.append))
    assert second["status"] == "succeeded"
    assert submitted == ["mock scene 1", "mock scene 2"]
    assert queries == ["task-1", "task-1", "task-2"]
    assert second["result"]["scene_ids"] == ["s1", "s2"]
    project["jobs"].append({**value, "status": "succeeded", "result": second["result"]})
    assert runtime._job_result(project, "scene", "s2")["path"].endswith("-2.mp4")


def test_ambiguous_batch_child_is_never_submitted_twice(monkeypatch, project, tmp_path):
    video = provider(monkeypatch)
    ready(monkeypatch)
    value = job("scene_batch", scene_id=None, scene_ids=["s1", "s2"])
    runtime._dump(tmp_path / "scenes/job-scene_batch-ledger.json", {"scene_ids": value["scene_ids"],
        "scene_results": [{"scene_id": "s1", "status": "submitting", "submission_started": True}]})
    result = run(runtime.execute_job(value, project, tmp_path))
    assert result["status"] == "blocked"
    assert len(result["result"]["scene_results"]) == 1


def test_batch_receipt_cannot_bypass_budget_for_unsubmitted_children(monkeypatch, project, tmp_path):
    provider(monkeypatch)
    ready(monkeypatch)
    value = job("scene_batch", scene_id=None, scene_ids=["s1", "s2"], provider_task_id="batch-ledger:job-scene_batch")
    value["budget_grant"]["max_cost"] = 0
    (tmp_path / "first.mp4").write_bytes(b"offline successful first child")
    runtime._dump(tmp_path / "scenes/job-scene_batch-ledger.json", {"scene_ids": value["scene_ids"],
        "scene_results": [{"scene_id": "s1", "status": "succeeded", "path": "first.mp4", "provider_task_id": "already-paid"}]})
    with pytest.raises(HTTPException, match="预算金额"):
        run(runtime.execute_job(value, project, tmp_path))


@pytest.mark.parametrize("burn_subtitles", [False, True])
def test_real_oldx_jumpcut_keeps_audio_sync_and_explicit_subtitle_choice(monkeypatch, project, tmp_path, burn_subtitles):
    """Synthetic tone/colour only; actual OLDX scripts and ffmpeg, no cloud calls."""
    import math
    import struct
    ffmpeg = find_test_ffmpeg()
    skill_python = os.environ.get("TEST_VIDEO_SKILL_PYTHON", "")
    if not ffmpeg or not Path(skill_python).is_file():
        pytest.skip("Set TEST_VIDEO_FFMPEG and TEST_VIDEO_SKILL_PYTHON for actual OLDX encoding")
    ready(monkeypatch)
    monkeypatch.setattr(runtime, "_ffmpeg", lambda: ffmpeg)
    monkeypatch.setattr(runtime, "_python", lambda: skill_python)
    project["mode"] = "avatar"
    project["plan"]["postproduction"] = {"trim_pauses": True, "burn_subtitles": burn_subtitles}
    project["plan"]["scenes"][0]["narration"] = "测试。"
    project["plan"]["scenes"][1]["narration"] = "结束。"
    project["plan"]["platform_variants"] = {key: {"title": f"{key}专用标题", "caption": f"{key}发布文案",
        "cover_direction": "标题留白", "layout_notes": "底部留出平台按钮区域"} for key in runtime.PLATFORMS}
    wd = runtime._skill_workdir(tmp_path, project)
    audio = wd / "out/merged.wav"
    audio.parent.mkdir(parents=True)
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        samples = [int(10000 * math.sin(2 * math.pi * 440 * index / 16000)) if index / 16000 < .8 or index / 16000 > 1.8 else 0 for index in range(41600)]
        output.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    (wd / "copy.txt").write_text("测试。结束。", encoding="utf-8")
    raw = wd / "avatar/final_avatar.mp4"
    raw.parent.mkdir()
    runtime._dump(raw.parent / "words_final.json", {"words": [{"word": "测试", "text": "测试。", "start": .1, "end": .7},
        {"word": "结束", "text": "结束。", "start": 1.9, "end": 2.5}]})
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=180x320:d=2.6:r=24",
        "-i", str(audio), "-t", "2.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(raw)], check=True)
    original = raw.read_bytes()
    project["jobs"] = [{"id": "v1", "kind": "voice", "revision": 1, "status": "succeeded", "result": {"audio_path": runtime._relative(tmp_path, audio)}},
        {"id": "a1", "kind": "avatar", "revision": 1, "status": "succeeded", "result": {"path": runtime._relative(tmp_path, raw)}}]
    project["reviews"]["postprocess"] = {"revision": 1, "decision": "approved", "quote": "测试用户看过原片，同意去停顿", "job_id": "a1"}
    result = run(runtime.execute_job(job("render"), project, tmp_path))
    assert result["status"] == "succeeded", result
    assert 1 < result["result"]["duration"] < 2.3
    assert result["result"]["jumpcut"]["removed_seconds"] > .6
    assert all(check["ok"] for check in result["result"]["jumpcut"]["checks"])
    assert raw.read_bytes() == original
    assert result["result"]["postproduction"]["burn_subtitles"] is burn_subtitles
    for export in result["result"]["exports"]:
        manifest = json.loads((tmp_path / export["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["title"] == f"{export['platform']}专用标题"
        assert manifest["caption"] == f"{export['platform']}发布文案"
        assert manifest["cover_direction"] == "标题留白"
        assert manifest["layout_notes"] == "底部留出平台按钮区域"
        assert manifest["subtitles_burned"] is burn_subtitles
        assert manifest["same_edit_as_master"] and manifest["same_cover_as_master"]
        assert (tmp_path / export["publication_path"]).is_file()
