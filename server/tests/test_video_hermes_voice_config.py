"""Offline credential wiring; no SSH connections, TTS, ASR or provider calls."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import video_hermes_config as primary
from app import video_hermes_voice_config as voice


def sample():
    return {"schema": 1, "provider": "bailian", "DASHSCOPE_API_KEY": "offline-dashscope-synthetic-key", "source": "hermes-profile-environment"}


@pytest.fixture
def memory_crypto(monkeypatch):
    vault = {}
    def crypt(value, *, decrypt):
        if decrypt:
            if value not in vault:
                raise voice.HermesVoiceConfigError("模拟密文无效")
            return vault[value]
        identifier = f"voice-cipher-{len(vault)}".encode()
        vault[identifier] = value
        return identifier
    monkeypatch.setattr(voice, "_crypto", crypt)
    return vault


def importer():
    path = Path(__file__).resolve().parents[2] / "scripts/import-hermes-voice-config.py"
    spec = importlib.util.spec_from_file_location("hermes_voice_import_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def args(tmp_path):
    identity, known = tmp_path / "synthetic-identity", tmp_path / "synthetic-known-hosts"
    identity.write_text("offline test identity")
    known.write_text("offline test host record")
    return SimpleNamespace(host="example.test", user="root", container="hermes", ssh="ssh", remote_home="/opt/data", identity=str(identity), known_hosts=str(known))


def test_missing_snapshot_is_optional_and_only_dashscope_can_be_exported(tmp_path):
    assert voice.load_hermes_voice_config(tmp_path / "missing.dpapi") == {}


def test_voice_ciphertext_and_loader_are_separate_from_primary(tmp_path, memory_crypto):
    target = tmp_path / "voice.dpapi"
    summary = voice.save_hermes_voice_config(sample(), target)
    assert summary["provider"] == "bailian" and "DASHSCOPE_API_KEY" not in summary
    assert sample()["DASHSCOPE_API_KEY"].encode() not in target.read_bytes()
    assert target.read_bytes().startswith(voice.MAGIC) and not target.read_bytes().startswith(primary.MAGIC)
    assert voice.load_hermes_voice_config(target) == {"DASHSCOPE_API_KEY": sample()["DASHSCOPE_API_KEY"]}
    assert not list(tmp_path.glob(".hermes-voice-*"))
    assert "DASHSCOPE_API_KEY" not in voice.describe_hermes_voice_config(target)


def test_voice_does_not_overwrite_without_explicit_replace(tmp_path, memory_crypto):
    target = tmp_path / "voice.dpapi"
    voice.save_hermes_voice_config(sample(), target)
    previous = target.read_bytes()
    replacement = {**sample(), "DASHSCOPE_API_KEY": "new-offline-synthetic-dashscope-key"}
    with pytest.raises(voice.HermesVoiceConfigError):
        voice.save_hermes_voice_config(replacement, target)
    assert target.read_bytes() == previous
    voice.save_hermes_voice_config(replacement, target, replace=True)
    assert voice.load_hermes_voice_config(target)["DASHSCOPE_API_KEY"] == replacement["DASHSCOPE_API_KEY"]


@pytest.mark.parametrize("change", [
    {"api_key": "must-not-leak-primary"}, {"DEEPSEEK_API_KEY": "must-not-leak-deepseek"},
    {"provider": "deepseek"}, {"schema": 2}, {"source": "untrusted-client"},
    {"DASHSCOPE_API_KEY": "${DASHSCOPE_REFERENCE}"}, {"DASHSCOPE_API_KEY": "must-not-leak\nnewline"},
    {"DASHSCOPE_API_KEY": "too-short"},
])
def test_primary_or_unparsed_or_invalid_values_are_rejected_safely(tmp_path, memory_crypto, change):
    with pytest.raises(voice.HermesVoiceConfigError) as error:
        voice.save_hermes_voice_config({**sample(), **change}, tmp_path / "voice.dpapi")
    assert "must-not-leak" not in str(error.value)
    assert not list(tmp_path.glob("*.dpapi"))


def test_bad_header_or_oversize_voice_snapshot_is_rejected(tmp_path, memory_crypto):
    target = tmp_path / "voice.dpapi"
    for data in (json.dumps(sample()).encode(), primary.MAGIC + b"other-domain", b"X" * (voice.MAX_BYTES + 1)):
        target.write_bytes(data)
        with pytest.raises(voice.HermesVoiceConfigError):
            voice.load_hermes_voice_config(target)


@pytest.mark.parametrize("link_method", ["is_symlink", "is_junction"])
def test_snapshot_cannot_cross_link_or_junction_ancestor(tmp_path, memory_crypto, monkeypatch, link_method):
    if not hasattr(Path, link_method):
        pytest.skip("Platform has no junction support")
    original = getattr(Path, link_method)
    monkeypatch.setattr(Path, link_method, lambda self: self.name == "unsafe-ancestor" or original(self))
    target = tmp_path / "unsafe-ancestor" / "voice.dpapi"
    with pytest.raises(voice.HermesVoiceConfigError):
        voice.load_hermes_voice_config(target)
    with pytest.raises(voice.HermesVoiceConfigError):
        voice.save_hermes_voice_config(sample(), target)


def test_voice_import_only_reads_correct_scoped_variable_over_strict_ssh(tmp_path, monkeypatch):
    module = importer()
    captured = {}
    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps(sample()), stderr="")
    monkeypatch.setattr(module._shared.subprocess, "run", run)
    assert module.read_remote_voice_config(args(tmp_path)) == sample()
    command = captured["command"]
    assert "StrictHostKeyChecking=yes" in command and "BatchMode=yes" in command
    assert command[-1].endswith("/python -B -")
    assert any(value.startswith("UserKnownHostsFile=") for value in command)
    assert sample()["DASHSCOPE_API_KEY"] not in repr(command)
    assert captured["errors"] == "replace" and captured["timeout"] == 25
    source = captured["input"]
    assert "load_env_file" in source and "DASHSCOPE_API_KEY" in source
    assert "DEEPSEEK_API_KEY" not in source and "model.get('api_key')" not in source
    assert not any(text in source for text in ("write_text", "write_bytes", "urlopen", "load_hermes_dotenv(", "requests.post"))


@pytest.mark.parametrize("field,value", [("host", "-oProxyCommand=bad"), ("user", "root;bad"), ("container", "hermes;bad"), ("remote_home", "/opt/data/../secrets")])
def test_voice_ssh_and_home_parameter_injection_are_rejected(tmp_path, monkeypatch, field, value):
    module = importer()
    settings = args(tmp_path)
    setattr(settings, field, value)
    monkeypatch.setattr(module._shared.subprocess, "run", lambda *a, **k: pytest.fail("SSH must not be started"))
    with pytest.raises(voice.HermesVoiceConfigError):
        module.read_remote_voice_config(settings)


def test_import_success_prints_metadata_only_and_writes_ciphertext(tmp_path, monkeypatch, capsys, memory_crypto):
    module = importer()
    settings = args(tmp_path)
    monkeypatch.setattr(module._shared.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(sample()), stderr=""))
    target = tmp_path / "voice.dpapi"
    result = module.main(["--host", settings.host, "--identity", settings.identity, "--known-hosts", settings.known_hosts, "--output", str(target)])
    output = capsys.readouterr().out
    assert result == 0 and sample()["DASHSCOPE_API_KEY"] not in output
    assert set(json.loads(output)) == {"provider", "configured", "source", "protection", "path"}
    assert sample()["DASHSCOPE_API_KEY"].encode() not in target.read_bytes()


def test_ssh_diagnostics_never_expose_voice_secrets(tmp_path, monkeypatch, capsys):
    module = importer()
    settings = args(tmp_path)
    secret = "NEVER_PRINT_SYNTHETIC_DASHSCOPE_KEY"
    monkeypatch.setattr(module._shared.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=secret, stderr=secret))
    result = module.main(["--host", settings.host, "--identity", settings.identity, "--known-hosts", settings.known_hosts, "--output", str(tmp_path / "voice.dpapi")])
    assert result == 1 and secret not in capsys.readouterr().out


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI CurrentUser check")
def test_real_dpapi_voice_roundtrip_and_primary_compatibility_are_isolated(tmp_path):
    primary_file, voice_file = tmp_path / "primary.dpapi", tmp_path / "voice.dpapi"
    primary_value = {"schema": 1, "provider": "custom", "model": "deepseek-flash", "base_url": "https://api.deepseek.com", "api_key": "different-offline-primary-synthetic-key"}
    primary.save_hermes_video_config(primary_value, primary_file)
    voice.save_hermes_voice_config(sample(), voice_file)
    assert primary.load_hermes_video_config(primary_file)["prompt_api_key"] == primary_value["api_key"]
    assert voice.load_hermes_voice_config(voice_file) == {"DASHSCOPE_API_KEY": sample()["DASHSCOPE_API_KEY"]}
    assert sample()["DASHSCOPE_API_KEY"].encode() not in voice_file.read_bytes()
    with pytest.raises(voice.HermesVoiceConfigError):
        voice.load_hermes_voice_config(primary_file)
    with pytest.raises(primary.HermesConfigError):
        primary.load_hermes_video_config(voice_file)
    # Replacing only the public header still cannot cross the crypto domains.
    forged = tmp_path / "forged-primary.dpapi"
    forged.write_bytes(primary.MAGIC + voice_file.read_bytes()[len(voice.MAGIC):])
    with pytest.raises(primary.HermesConfigError):
        primary.load_hermes_video_config(forged)
