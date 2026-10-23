"""Offline Qwen voice wiring review. Synthetic keys; no service or model calls."""
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from app import video_runtime as runtime

ROOT = SERVER.parent
VOICE = ROOT / "hermes-skill/oldx-avatar/scripts/voice"


def startup():
    spec = importlib.util.spec_from_file_location("voice_startup_review", SERVER / "scripts/run_video_local.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def production_function(name, filename, namespace):
    """Run the production key/request function while replacing all I/O dependencies."""
    tree = ast.parse((VOICE / filename).read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("configuration", [
    {"DASHSCOPE_API_KEY": " synthetic-manual-key "},
    {"BL_KEY_FILE": " private-manual-key-path "},
    {"DASHSCOPE_API_KEY": "synthetic-manual-key", "BL_KEY_FILE": "private-manual-key-path"},
])
def test_manual_voice_credentials_win_without_decrypting_snapshot(configuration, tmp_path):
    def unused_loader(_):
        pytest.fail("Manual voice configuration must not decrypt unused Hermes snapshot")
    result, source = startup().resolve_voice_environment(configuration, tmp_path, loader=unused_loader)
    assert source == "local"
    assert result == {key: value.strip() for key, value in configuration.items()}


def test_blank_template_uses_independent_voice_snapshot_without_prompt_key(tmp_path):
    calls = []
    def loader(path):
        calls.append(path)
        return {"DASHSCOPE_API_KEY": "synthetic-qwen-key"}
    configuration = {"DASHSCOPE_API_KEY": " ", "BL_KEY_FILE": "", "prompt_api_key": "synthetic-deepseek-key", "RUNNINGHUB_API_KEY": "synthetic-rh-key"}
    result, source = startup().resolve_voice_environment(configuration, tmp_path, loader=loader)
    assert result == {"DASHSCOPE_API_KEY": "synthetic-qwen-key"}
    assert source == "hermes"
    assert calls == [tmp_path / "hermes-voice-config.dpapi"]
    assert configuration["prompt_api_key"] == "synthetic-deepseek-key"
    assert configuration["RUNNINGHUB_API_KEY"] == "synthetic-rh-key"


def test_missing_voice_snapshot_never_falls_back_to_deepseek(tmp_path):
    result, source = startup().resolve_voice_environment({"prompt_api_key": "synthetic-deepseek-key"}, tmp_path, loader=lambda _: {})
    assert result == {} and source == "unconfigured"


def test_controlled_child_environment_inherits_qwen_key_and_private_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.os, "environ", {"DASHSCOPE_API_KEY": "synthetic-qwen-key", "TASK_VIDEO_PAID_ENABLED": "0", "TASK_VIDEO_VOICE_CREDENTIAL_SOURCE": "hermes"})
    result = runtime._child_env(tmp_path)
    assert result["DASHSCOPE_API_KEY"] == "synthetic-qwen-key"
    assert result["TASK_VIDEO_PAID_ENABLED"] == "0"
    assert result["TASK_VIDEO_VOICE_CREDENTIAL_SOURCE"] == "hermes"
    assert result["DH_WORKSPACE"] == str(tmp_path)
    assert result["OLDX_HERMES"] == "1"
    assert result["PYTHONDONTWRITEBYTECODE"] == "1"


def test_capabilities_do_not_expose_key_or_imply_live_voice_rights(monkeypatch, tmp_path):
    key = "synthetic-qwen-key-never-expose"
    monkeypatch.setattr(runtime.os, "environ", {"DASHSCOPE_API_KEY": key, "TASK_VIDEO_PAID_ENABLED": "0", "TASK_VIDEO_VOICE_CREDENTIAL_SOURCE": "hermes"})
    monkeypatch.setattr(runtime, "_settings", lambda: SimpleNamespace(prompt_model="deepseek-flash", prompt_api_key="synthetic-deepseek-key", seedance_api_key=""))
    monkeypatch.setattr(runtime, "_python", lambda: sys.executable)
    monkeypatch.setattr(runtime, "_ffmpeg", lambda: "offline-ffmpeg")
    capabilities = runtime.runtime_capabilities()
    assert capabilities["voice"]["credentials_configured"] is True
    assert capabilities["voice"]["credential_source"] == "hermes"
    assert capabilities["voice"]["paid_enabled"] is False
    assert capabilities["voice"]["ready"] is False
    assert key not in json.dumps(capabilities)
    assert "synthetic-deepseek-key" not in json.dumps(capabilities)


def key_finder(environment, files):
    reads = []
    def fake_open(path, **kwargs):
        reads.append(path)
        return io.StringIO(files[path])
    fake_os = SimpleNamespace(environ=environment, path=SimpleNamespace(isfile=lambda path: path in files))
    namespace = {"os": fake_os, "open": fake_open, "DEFAULT_KEY_FILE": "project/.bailian/api_key"}
    return production_function("find_key", "common.py", namespace), reads


def test_oldx_environment_key_precedes_key_files_without_reading_them():
    find, reads = key_finder({"DASHSCOPE_API_KEY": " synthetic-qwen-key ", "BL_KEY_FILE": "manual.key"},
                             {"manual.key": "synthetic-file-key", "project/.bailian/api_key": "synthetic-default-key"})
    key, description = find()
    assert key == "synthetic-qwen-key" and reads == []
    assert "DASHSCOPE_API_KEY" in description and key not in description


def test_oldx_manual_key_file_precedes_project_fallback():
    find, reads = key_finder({"DASHSCOPE_API_KEY": "", "BL_KEY_FILE": "manual.key"},
                             {"manual.key": " synthetic-file-key ", "project/.bailian/api_key": "synthetic-default-key"})
    key, description = find()
    assert key == "synthetic-file-key" and reads == ["manual.key"]
    assert key not in description


def test_oldx_does_not_use_deepseek_as_voice_credential():
    find, reads = key_finder({"DEEPSEEK_API_KEY": "synthetic-deepseek-key", "TASK_PROMPT_API_KEY": "synthetic-text-key"}, {})
    assert find() == (None, None)
    assert reads == []


def test_hermes_default_key_file_is_project_scoped_not_user_home(tmp_path):
    tree = ast.parse((VOICE / "common.py").read_text(encoding="utf-8"))
    expression = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "DEFAULT_KEY_FILE" for target in node.targets))
    import os
    fake_os = SimpleNamespace(environ={"OLDX_HERMES": "1"}, path=SimpleNamespace(join=os.path.join, expanduser=lambda _: pytest.fail("Hermes must not use user home key fallback")))
    path = eval(compile(ast.Expression(expression), "common.py", "eval"), {"os": fake_os, "WORKSPACE_ROOT": str(tmp_path)})
    assert Path(path) == tmp_path / ".bailian/api_key"


def test_actual_tts_and_asr_request_builders_share_qwen_key_without_network():
    constants = {}
    for node in ast.parse((VOICE / "common.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"MODEL", "ASR_MODEL", "TTS_URL", "ASR_URL"}:
                constants[node.targets[0].id] = ast.literal_eval(node.value)
    assert constants["MODEL"] == "qwen-audio-3.1-tts-flash"
    assert constants["ASR_MODEL"] == "qwen-audio-3.1-asr-flash"
    captured = []
    def post(url, body, key, **kwargs):
        captured.append((url, body, key))
        return 401, {"message": "offline synthetic refusal"}
    c = SimpleNamespace(**constants, MAX_TRIES=1, SAMPLE_RATE=48000, INSTRUCTION="offline", KEY_PAGE="offline-key-help", post_json=post,
                        to_pcm_wav=lambda *args, **kwargs: None)
    fake_os = SimpleNamespace(path=SimpleNamespace(join=lambda *parts: "/".join(parts), isfile=lambda _: False, getsize=lambda _: 16), remove=lambda _: None)
    synthetic_key = "synthetic-one-qwen-key"
    tts = production_function("synth_text", "synth.py", {"os": fake_os, "C": c, "build_text": lambda text: text,
        "cache_key": lambda *args: "offline", "pace": lambda: None, "time": SimpleNamespace(time=lambda: 0), "FATAL": {401, 403}, "Fatal": RuntimeError})
    with pytest.raises(RuntimeError):
        tts("测试", "synthetic-voice", synthetic_key, "cache", "offline")
    asr = production_function("transcribe", "asr.py", {"os": fake_os, "C": c, "hashlib": hashlib,
        "open": lambda *args, **kwargs: io.BytesIO(b"synthetic wav bytes"), "tempfile": SimpleNamespace(gettempdir=lambda: "temp"),
        "base64": __import__("base64"), "_pace": lambda: None})
    with pytest.raises(PermissionError):
        asr("synthetic.wav", synthetic_key)
    assert len(captured) == 2
    assert [entry[2] for entry in captured] == [synthetic_key, synthetic_key]
    assert captured[0][0].startswith("https://dashscope.aliyuncs.com/")
    assert captured[1][0].startswith("https://dashscope.aliyuncs.com/")
    assert captured[0][1]["model"] == constants["MODEL"]
    assert captured[1][1]["model"] == constants["ASR_MODEL"]
