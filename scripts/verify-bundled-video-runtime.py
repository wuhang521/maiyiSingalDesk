"""Exercise the shipped interpreter, real local rendering and fresh-user sidecar.

No provider/API keys, saved users, cloud requests or paid generation are used.
The relocated bundle and synthetic media live only in the dedicated scratch tree.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import wave


ROOT = Path(__file__).resolve().parents[1]


def probe(runtime: Path, scratch: Path) -> dict:
    import numpy as np
    import scipy.signal
    import cv2
    from PIL import Image
    import jieba
    import imageio_ffmpeg
    import librosa
    import soundfile
    import fastapi
    import sqlalchemy
    import uvicorn
    import httpx
    import pydantic_settings
    import importlib.metadata
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    assert Path(sys.executable).resolve() == (runtime / "python/python.exe").resolve()
    assert sys.prefix == sys.base_prefix and sys.flags.isolated == 1
    assert not any("site-packages" in path and not Path(path).is_relative_to(runtime) for path in sys.path)
    distributions = list(importlib.metadata.distributions())
    installed = {canonicalize_name(item.metadata["Name"]): item.version for item in distributions}
    assert not {"pip", "pytest", "langchain-community"}.intersection(installed)
    for distribution in distributions:
        for value in distribution.requires or []:
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                version = installed.get(canonicalize_name(requirement.name))
                assert version is not None and version in requirement.specifier, f"Dependency mismatch: {distribution.metadata['Name']} requires {requirement}"
    samples = np.sin(np.arange(1600, dtype=np.float64) * 2 * np.pi * 200 / 16000).astype(np.float32)
    assert scipy.signal.resample(samples, 800).shape == (800,)
    assert librosa.resample(samples, orig_sr=16000, target_sr=8000).shape == (800,)
    pitch = librosa.yin(samples, fmin=80, fmax=400, sr=16000, frame_length=512)
    assert np.isfinite(pitch).all()
    identity = hashlib.sha256(b"offline-runtime-account").hexdigest()
    profile = scratch / "profile"
    account = profile / "accounts" / identity
    account.mkdir(parents=True)
    assert len(str(account)) >= 126
    runner_spec = importlib.util.spec_from_file_location("bundled_runner_probe", runtime / "server/scripts/run_video_desktop.py")
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    cache = runner.bundled_cache_directory(profile / "config", identity)
    assert Path(os.environ["NUMBA_CACHE_DIR"]) == cache
    cache_files = [target for target in cache.rglob("*") if target.suffix in {".nbi", ".nbc"}]
    assert cache_files and max(len(str(target)) for target in cache_files) < 260
    assert np.isfinite(librosa.yin(samples, fmin=80, fmax=400, sr=16000, frame_length=512)).all()
    soundfile.write(scratch / "sample.wav", samples, 16000)
    decoded, rate = soundfile.read(scratch / "sample.wav")
    assert rate == 16000 and len(decoded) == len(samples)
    image = scratch / "sample.png"
    Image.new("RGB", (48, 48), (20, 100, 150)).save(image)
    assert cv2.imdecode(np.frombuffer(image.read_bytes(), np.uint8), cv2.IMREAD_COLOR).shape == (48, 48, 3)
    assert list(jieba.cut("视频运行环境验证"))
    manifest = json.loads((runtime / "runtime.json").read_text("utf-8"))
    ffmpeg = str((runtime / manifest["ffmpeg"]).resolve())
    assert Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve() == Path(ffmpeg)
    version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    sys.path.insert(0, str(runtime / "server"))
    from app import video_runtime as engine
    import app.config as configuration
    configuration.get_settings = lambda: configuration.Settings(_env_file=None, api_key="", prompt_model="", seedance_api_key="", database_url="sqlite:///:memory:")
    os.environ.update(TASK_VIDEO_PYTHON=sys.executable, TASK_VIDEO_FFMPEG=ffmpeg,
                      TASK_VIDEO_SKILL_DIR=str(runtime / "hermes-skill/oldx-avatar"), TASK_VIDEO_PAID_ENABLED="0")
    def forbidden_cloud(*args, **kwargs):
        raise AssertionError("Offline runtime verification must not submit a provider request")
    engine.httpx.AsyncClient = forbidden_cloud
    cli = scratch / "skill-project"
    (cli / "out").mkdir(parents=True)
    text = "离线运行环境验证。"
    (cli / "copy.txt").write_text(text, encoding="utf-8")
    shutil.copyfile(scratch / "sample.wav", cli / "out/merged.wav")
    (cli / "out/words.json").write_text(json.dumps({"audio_sha": hashlib.sha256((cli / "out/merged.wav").read_bytes()).hexdigest()[:16], "total_seconds": 0.1,
        "words": [{"word": char, "text": char, "start": index * 0.1 / len(text), "end": (index + 1) * 0.1 / len(text)} for index, char in enumerate(text)]}, ensure_ascii=False), encoding="utf-8")
    assert asyncio.run(engine._cli(cli, "review.py", "init", "--workdir", cli))["review_enabled"]
    assert asyncio.run(engine._cli(cli, "review.py", "status", "--workdir", cli))["review_enabled"]
    for script in ("voice/project.py", "avatar/prepare_image.py", "voice/synth.py"):
        assert asyncio.run(engine._cli(cli, script, "--help"))["ok"]
    assert asyncio.run(engine._cli(cli, "pipeline.py", "segments", "--workdir", cli))["ok"]
    padding = 187 - len(str(scratch.resolve()))
    assert 1 <= padding <= 240, "Use a shorter --scratch directory for the 188-character path regression"
    work = scratch.resolve() / ("r" * padding)
    work.mkdir()
    assert len(str(work)) == 188
    voice = work / "voice.wav"
    with wave.open(str(voice), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes((np.tile(samples, 20) * 8000).astype("<i2").tobytes())
    scenes, jobs = [], []
    for index, color in enumerate(("blue", "green"), 1):
        path = work / f"scene{index}.mp4"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=180x320:d=1:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
        scenes.append({"id": f"s{index}", "title": "Offline fixture", "duration": 4, "narration": f"Offline fixture {index}."})
        jobs.append({"id": f"existing-scene-{index}", "kind": "scene", "scene_id": f"s{index}", "revision": 1,
                     "status": "succeeded", "result": {"path": path.name}})
    jobs.append({"id": "existing-voice", "kind": "voice", "revision": 1, "status": "succeeded", "result": {"audio_path": voice.name}})
    project = {"id": "offline-portability-fixture", "revision": 1, "mode": "standard", "topic": "Offline runtime test",
        "platforms": ["视频号", "抖音", "小红书"], "plan": {"brief": {"title": "Offline runtime test"}, "audio_plan": {}, "scenes": scenes},
        "reviews": {stage: {"revision": 1, "decision": "approved", "quote": "Synthetic test fixture only"} for stage in ("inputs", "voice", "generation")}, "jobs": jobs}
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (voice, work / "scene1.mp4", work / "scene2.mp4")}
    outcome = asyncio.run(engine.execute_job({"id": "runtime-portability-render", "kind": "render", "revision": 1}, project, work))
    assert outcome["status"] == "succeeded", outcome
    assert len(outcome["result"]["exports"]) == 3
    for export in outcome["result"]["exports"]:
        for key in ("path", "manifest_path", "cover_path", "subtitle_path", "publication_path"):
            target = work / export[key]
            assert target.is_file() and target.stat().st_size and len(str(target)) < 260
    assert before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (voice, work / "scene1.mp4", work / "scene2.mp4")}
    final = work / outcome["result"]["path"]
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(final), "-f", "null", "-"], check=True)
    return {"python": sys.version.split()[0], "isolated": bool(sys.flags.isolated), "host_packages_used": False,
        "imports": ["fastapi", "uvicorn", "sqlalchemy", "httpx", "pydantic_settings", "numpy", "scipy", "cv2", "PIL", "jieba", "imageio_ffmpeg", "librosa", "soundfile"],
        "signal_audio_image_checks": True, "dependency_versions_consistent": True, "ffmpeg": version, "render": {"root_characters": len(str(work)), "exports": 3,
        "duration": outcome["result"]["duration"], "decoded": True, "original_media_unchanged": True},
        "jit_cache": {"simulated_account_path_characters": len(str(account)), "files": len(cache_files), "maximum_path_characters": max(len(str(target)) for target in cache_files), "disk_cache_reused": True},
        "skill_cli": {"review_init_status": True, "entrypoint_help": True, "offline_segments": True}, "paid_requests": 0}


def sidecar(runtime: Path, scratch: Path, environment: dict) -> dict:
    import urllib.request
    import queue
    import threading
    config = scratch / "empty-config"
    config.mkdir()
    envelope = {"data_dir": str(scratch / "new-account"), "config_dir": str(config),
        "identity": {"backend_url": "https://example.test", "id": 7, "username": "offline-fixture", "display_name": "中文测试用户"},
        "capability_token": "a" * 48, "control_token": "b" * 48, "instance_id": "offline-runtime-fixture", "cors_origins": ["null"]}
    process = subprocess.Popen([str(runtime / "python/python.exe"), "-I", "-B", "-X", "utf8", str(runtime / "server/scripts/run_video_desktop.py")],
        cwd=scratch, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        process.stdin.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        process.stdin.flush()
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        line = lines.get(timeout=45)
        assert line, process.stderr.read()
        ready = json.loads(line)
        assert ready["event"] == "ready" and ready["instance_id"] == envelope["instance_id"]
        base = f"http://127.0.0.1:{ready['port']}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def get(path, token):
            request = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
            with opener.open(request, timeout=20) as response:
                return json.load(response)
        status = get("/desktop/status", envelope["capability_token"])
        assert status["service"] == "maiyi-desktop-video"
        restart = get("/desktop/update-safety", envelope["control_token"])
        assert restart["safe_to_restart"] is True
        caps = get("/api/v1/video/projects/capabilities", envelope["capability_token"])
        # The fresh user owns no credentials; local rendering still works.
        assert caps["render"]["ready"] is True, caps
        assert caps["voice"]["ready"] is False and caps["scene"]["ready"] is False, caps
        assert caps["planning"]["ready"] is False, caps
        assert not list(config.iterdir()), "No private settings should be imported into a new account"
        return {"started": True, "new_user_requires_provider_keys": True, "local_render_ready": True, "private_config_imported": False,
                "control_update_safety_checked": True}
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / "artifacts/bundled-video-runtime")
    parser.add_argument("--scratch", type=Path, default=ROOT / "artifacts/video-runtime-tests")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if args.probe:
        print(json.dumps(probe(runtime, args.scratch.resolve()), ensure_ascii=False))
        return 0
    spec = importlib.util.spec_from_file_location("bundle_builder", Path(__file__).with_name("package-video-runtime.py"))
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    inventory = builder.verify(runtime)
    args.scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="portable-", dir=args.scratch) as temporary:
        scratch = Path(temporary).resolve()
        moved = scratch / "迁移 安装/video-runtime"
        shutil.copytree(runtime, moved)
        inventory_before = builder.verify(moved)
        environment = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}}
        ffmpeg = moved / json.loads((moved / "runtime.json").read_text("utf-8"))["ffmpeg"]
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PATH=str(ffmpeg.parent),
            IMAGEIO_FFMPEG_EXE=str(ffmpeg), NUMBA_CACHE_DIR=str(scratch / "profile/cache/numba" / hashlib.sha256(b"offline-runtime-account").hexdigest()[:24]), JIEBA_CACHE_DIR=str(scratch / "jieba-cache"))
        result = subprocess.run([str(moved / "python/python.exe"), "-I", "-B", "-X", "utf8", str(Path(__file__).resolve()), "--probe", "--runtime", str(moved), "--scratch", str(scratch)],
            cwd=scratch, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=240)
        if result.returncode:
            raise RuntimeError(result.stderr[-6000:])
        outcome = json.loads(result.stdout.splitlines()[-1])
        outcome["sidecar"] = sidecar(moved, scratch, environment)
        assert builder.verify(moved)["manifest_sha256"] == inventory_before["manifest_sha256"]
        outcome.update(relocated_into_unicode_path=True, original_inventory_unchanged=True, inventory=inventory)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
