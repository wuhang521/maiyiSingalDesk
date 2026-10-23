"""Offline video credential isolation. Fixtures never call SSH or providers."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import video_hermes_config as primary
from app import video_hermes_scene_config as scene


def fixture_value():
    return {"schema": 1, "provider": "ark", "model": "doubao-seedance-offline-fixture",
            "base_url": scene.ARK_BASE, "api_key": "synthetic-offline-video-key"}


def load_script(relative, name):
    path = Path(__file__).resolve().parents[2] / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def memory_crypto(monkeypatch):
    vault = {}
    def crypt(value, *, decrypt):
        if decrypt:
            return vault[value]
        token = f"synthetic-cipher-{len(vault)}".encode()
        vault[token] = value
        return token
    monkeypatch.setattr(scene, "_crypto", crypt)


def test_snapshot_roundtrip_exports_only_scene_credentials(tmp_path, memory_crypto):
    path = tmp_path / "scene.dpapi"
    summary = scene.save_hermes_scene_config(fixture_value(), path)
    assert summary["configured"] and "api_key" not in summary
    assert b"synthetic-offline-video-key" not in path.read_bytes()
    assert path.read_bytes().startswith(scene.MAGIC)
    assert not path.read_bytes().startswith(primary.MAGIC)
    assert scene.load_hermes_scene_config(path) == {
        "seedance_api_key": fixture_value()["api_key"], "seedance_base_url": scene.ARK_BASE,
        "seedance_model": fixture_value()["model"]}
    assert not list(tmp_path.glob(".hermes-scene-*"))


@pytest.mark.parametrize("patch", [
    {"provider": "custom"}, {"base_url": "https://untrusted.example.test"},
    {"api_key": "${ARK_API_KEY}"}, {"model": "deepseek-flash"}, {"extra_key": "not-allowed"},
])
def test_snapshot_rejects_wrong_scope_or_unresolved_keys(tmp_path, patch):
    with pytest.raises(scene.HermesSceneConfigError):
        scene.save_hermes_scene_config({**fixture_value(), **patch}, tmp_path / "invalid.dpapi")
    assert not list(tmp_path.iterdir())


def test_existing_snapshot_needs_explicit_replace(tmp_path, memory_crypto):
    path = tmp_path / "scene.dpapi"
    scene.save_hermes_scene_config(fixture_value(), path)
    original = path.read_bytes()
    replacement = {**fixture_value(), "api_key": "second-synthetic-offline-key"}
    with pytest.raises(scene.HermesSceneConfigError):
        scene.save_hermes_scene_config(replacement, path)
    assert path.read_bytes() == original
    scene.save_hermes_scene_config(replacement, path, replace=True)
    assert scene.load_hermes_scene_config(path)["seedance_api_key"] == replacement["api_key"]


def test_primary_envelope_is_not_accepted_as_scene(tmp_path):
    path = tmp_path / "wrong.dpapi"
    path.write_bytes(primary.MAGIC + b"synthetic-ciphertext")
    with pytest.raises(scene.HermesSceneConfigError):
        scene.load_hermes_scene_config(path)
    assert scene.load_hermes_scene_config(tmp_path / "missing.dpapi") == {}


def test_explicit_local_video_key_does_not_decrypt_snapshot(tmp_path):
    startup = load_script("server/scripts/run_video_local.py", "scene_startup_test")
    def forbidden(_):
        raise AssertionError("unused snapshot must not be decrypted")
    result, source = startup.resolve_scene_settings({"seedance_api_key": "synthetic-manual-key"}, tmp_path, loader=forbidden)
    assert result == {"seedance_api_key": "synthetic-manual-key"} and source == "local"


def test_scene_fallback_has_separate_source_and_no_prompt_fields(tmp_path):
    startup = load_script("server/scripts/run_video_local.py", "scene_fallback_test")
    expected = {"seedance_api_key": "synthetic-inherited-key", "seedance_model": "ep-fixture"}
    loaded = []
    result, source = startup.resolve_scene_settings({}, tmp_path, loader=lambda path: loaded.append(path) or expected)
    assert source == "hermes" and result == expected
    assert loaded == [tmp_path / "hermes-scene-config.dpapi"]
    assert startup.resolve_scene_settings({}, tmp_path, loader=lambda _: {}) == ({}, "unconfigured")


def test_remote_probe_only_reads_ark_profile_and_rejects_injection():
    importer = load_script("scripts/import-hermes-scene-config.py", "scene_import_test")
    probe = importer.remote_probe("/opt/data", "ep-offline-fixture")
    assert "load_env_file" in probe and "profile.get('ARK_API_KEY')" in probe
    assert "write_text" not in probe and "DASHSCOPE_API_KEY" not in probe
    with pytest.raises(scene.HermesSceneConfigError):
        importer.remote_probe("/opt/data;write", "ep-offline-fixture")
    with pytest.raises(scene.HermesSceneConfigError):
        importer.remote_probe("/opt/data", "ep-fixture;write")


@pytest.mark.skipif(os.name != "nt", reason="Windows CurrentUser DPAPI")
def test_real_dpapi_purpose_separation(tmp_path):
    path = tmp_path / "real-synthetic.dpapi"
    scene.save_hermes_scene_config(fixture_value(), path)
    assert scene.load_hermes_scene_config(path)["seedance_api_key"] == fixture_value()["api_key"]
    encrypted = path.read_bytes()[len(scene.MAGIC):]
    with pytest.raises(primary.HermesConfigError):
        primary._dpapi(encrypted, decrypt=True)
