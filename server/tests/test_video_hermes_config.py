"""Offline DPAPI/import contract tests. No SSH connections or provider requests."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import video_hermes_config as config


def sample():
    return {"schema": 1, "provider": "custom", "model": "deepseek-flash", "base_url": "https://api.deepseek.com", "api_key": "offline-synthetic-key-only"}


@pytest.fixture
def memory_crypto(monkeypatch):
    vault = {}
    def crypt(value, *, decrypt):
        if decrypt:
            if value not in vault:
                raise config.HermesConfigError("测试密文不可解密")
            return vault[value]
        token = f"offline-ciphertext-{len(vault)}".encode()
        vault[token] = value
        return token
    monkeypatch.setattr(config, "_dpapi", crypt)
    return vault


def importer():
    path = Path(__file__).resolve().parents[2] / "scripts/import-hermes-video-config.py"
    spec = importlib.util.spec_from_file_location("hermes_video_import_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def args(tmp_path, **extra):
    identity, hosts = tmp_path / "identity", tmp_path / "known_hosts"
    identity.write_text("synthetic identity fixture")
    hosts.write_text("synthetic known-host fixture")
    return SimpleNamespace(host="example.test", user="root", container="hermes", ssh="ssh", remote_config="/opt/data/config.yaml", identity=str(identity), known_hosts=str(hosts), **extra)


def test_missing_snapshot_is_optional_and_does_not_touch_other_providers(tmp_path):
    assert config.load_hermes_video_config(tmp_path / "missing.dpapi") == {}


def test_ciphertext_only_and_fixed_three_settings_fields(tmp_path, memory_crypto):
    path = tmp_path / "provider.dpapi"
    summary = config.save_hermes_video_config(sample(), path)
    assert summary["model"] == "deepseek-flash" and "api_key" not in summary
    assert sample()["api_key"].encode() not in path.read_bytes()
    assert not list(tmp_path.glob(".hermes-provider-*"))
    assert config.load_hermes_video_config(path) == {"prompt_model": "deepseek-flash", "prompt_base_url": "https://api.deepseek.com", "prompt_api_key": sample()["api_key"]}
    assert set(config.describe_hermes_video_config(path)) == {"provider", "model", "configured", "protection"}


def test_no_implicit_snapshot_replacement(tmp_path, memory_crypto):
    path = tmp_path / "provider.dpapi"
    config.save_hermes_video_config(sample(), path)
    previous = path.read_bytes()
    with pytest.raises(config.HermesConfigError):
        config.save_hermes_video_config({**sample(), "model": "replacement"}, path)
    assert path.read_bytes() == previous
    config.save_hermes_video_config({**sample(), "model": "replacement"}, path, replace=True)
    assert config.load_hermes_video_config(path)["prompt_model"] == "replacement"


@pytest.mark.parametrize("change", [
    {"unknown_secret": "must-not-leak"}, {"base_url": "http://example.test"},
    {"base_url": "https://account:must-not-leak@example.test"}, {"api_key": "must-not-leak\ninvalid"},
    {"model": "sk-must-not-leak"}, {"schema": 2}, {"api_key": "${UNRESOLVED_MODEL_KEY}"},
])
def test_unapproved_fields_or_unusable_config_fail_without_secret_errors(tmp_path, memory_crypto, change):
    with pytest.raises(config.HermesConfigError) as error:
        config.save_hermes_video_config({**sample(), **change}, tmp_path / "model.dpapi")
    assert "must-not-leak" not in str(error.value)
    assert not list(tmp_path.glob("*.dpapi"))


def test_plaintext_or_oversize_snapshot_is_rejected(tmp_path, memory_crypto):
    path = tmp_path / "model.dpapi"
    for content in (json.dumps(sample()).encode(), b"a" * (config.MAX_BYTES + 1)):
        path.write_bytes(content)
        with pytest.raises(config.HermesConfigError):
            config.load_hermes_video_config(path)


def test_link_components_are_rejected_before_resolving(tmp_path, memory_crypto, monkeypatch):
    original = Path.is_symlink
    path = tmp_path / "linked-parent" / "model.dpapi"
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "linked-parent" or original(self))
    with pytest.raises(config.HermesConfigError):
        config.load_hermes_video_config(path)
    with pytest.raises(config.HermesConfigError):
        config.save_hermes_video_config(sample(), path)


def test_ssh_is_strict_read_only_and_secret_never_enters_command_line(tmp_path, monkeypatch):
    module = importer()
    captured = {}
    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps(sample()), stderr="")
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.read_remote_config(args(tmp_path))["model"] == "deepseek-flash"
    assert "StrictHostKeyChecking=yes" in captured["command"] and "BatchMode=yes" in captured["command"]
    assert any(value.startswith("UserKnownHostsFile=") for value in captured["command"])
    assert captured["errors"] == "replace"
    assert sample()["api_key"] not in repr(captured["command"])
    assert "docker exec -i hermes" in captured["command"][-1]
    assert not any(word in captured["input"] for word in ("write_text", "write_bytes", "urlopen", "requests.post"))


@pytest.mark.parametrize("field,value", [("host", "-oProxyCommand=bad"), ("user", "root;touch bad"), ("container", "hermes;touch bad"), ("remote_config", "/opt/data/../secrets")])
def test_ssh_and_remote_path_injection_is_rejected(tmp_path, monkeypatch, field, value):
    module = importer()
    settings = args(tmp_path)
    setattr(settings, field, value)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("must not start SSH"))
    with pytest.raises(config.HermesConfigError):
        module.read_remote_config(settings)


def test_importer_never_echoes_remote_stderr_or_secret_stdout(tmp_path, monkeypatch, capsys):
    module = importer()
    key = "NEVER_ECHO_SYNTHETIC_SECRET"
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=key, stderr=key))
    options = args(tmp_path)
    result = module.main(["--host", options.host, "--identity", options.identity, "--known-hosts", options.known_hosts, "--output", str(tmp_path / "provider.dpapi")])
    assert result == 1 and key not in capsys.readouterr().out


def test_successful_import_only_outputs_safe_metadata(tmp_path, monkeypatch, capsys, memory_crypto):
    module = importer()
    options = args(tmp_path)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(sample()), stderr=""))
    path = tmp_path / "imported.dpapi"
    result = module.main(["--host", options.host, "--identity", options.identity, "--known-hosts", options.known_hosts, "--output", str(path)])
    output = capsys.readouterr().out
    assert result == 0 and sample()["api_key"] not in output
    assert set(json.loads(output)) == {"provider", "model", "configured", "protection", "path"}
    assert sample()["api_key"].encode() not in path.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI current-user verification")
def test_real_current_user_dpapi_roundtrip_uses_synthetic_credentials_only(tmp_path):
    path = tmp_path / "synthetic.dpapi"
    config.save_hermes_video_config(sample(), path)
    assert config.load_hermes_video_config(path)["prompt_api_key"] == sample()["api_key"]
    assert sample()["api_key"].encode() not in path.read_bytes()
