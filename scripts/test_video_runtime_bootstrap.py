"""Offline tests for installation-bound video tool selection."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("video_desktop_runner", Path(__file__).resolve().parents[1] / "server/scripts/run_video_desktop.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class BundledBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / "artifacts/video-runtime-tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="video-bootstrap-", dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = {"schema": 2, "platform": "win32", "arch": "x64", "python": "python/python.exe", "ffmpeg": "tools/ffmpeg.exe"}
        for relative in (self.manifest["python"], self.manifest["ffmpeg"]):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"test placeholder")

    def save(self):
        (self.root / "runtime.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def resolve(self):
        with patch.object(RUNNER.sys, "executable", str(self.root / "python/python.exe")):
            return RUNNER.bundled_environment(self.root)

    def test_missing_or_legacy_manifest_preserves_local_mode(self):
        self.assertEqual(self.resolve(), {})
        self.manifest["schema"] = 1
        self.save()
        self.assertEqual(self.resolve(), {})

    def test_only_tool_paths_are_derived_from_bundle(self):
        self.save()
        actual = self.resolve()
        self.assertEqual(actual, {"TASK_VIDEO_PYTHON": str((self.root / "python/python.exe").resolve()),
                                  "TASK_VIDEO_FFMPEG": str((self.root / "tools/ffmpeg.exe").resolve())})
        existing = {"TASK_VIDEO_PYTHON": "D:/old-machine/python.exe", "TASK_VIDEO_FFMPEG": "C:/old-machine/ffmpeg.exe",
                    "DASHSCOPE_API_KEY": "synthetic-provider-key", "TASK_VIDEO_SCENE_PROVIDER": "seedance"}
        merged = {**existing, **actual}
        self.assertEqual(merged["DASHSCOPE_API_KEY"], existing["DASHSCOPE_API_KEY"])
        self.assertEqual(merged["TASK_VIDEO_SCENE_PROVIDER"], "seedance")
        self.assertEqual(existing["TASK_VIDEO_PYTHON"], "D:/old-machine/python.exe")

    def test_bad_paths_are_rejected(self):
        for value in ("../python.exe", "/python.exe", "C:/python.exe", "python\\python.exe", "python//python.exe", "python/./python.exe", "python/python.exe:stream"):
            with self.subTest(value=value):
                self.manifest["python"] = value
                self.save()
                with self.assertRaises(ValueError):
                    self.resolve()

    def test_missing_tool_is_not_replaced_by_path_lookup(self):
        (self.root / "tools/ffmpeg.exe").unlink()
        self.save()
        with self.assertRaises(ValueError):
            self.resolve()

    def test_external_interpreter_cannot_start_a_bundled_runtime(self):
        self.save()
        with patch.object(RUNNER.sys, "executable", "C:/other/python.exe"), self.assertRaises(ValueError):
            RUNNER.bundled_environment(self.root)

    def test_other_architecture_is_rejected(self):
        self.manifest["arch"] = "arm64"
        self.save()
        with self.assertRaises(ValueError):
            self.resolve()

    def test_jit_cache_does_not_inherit_the_long_account_path(self):
        home = self.root / "video-production"
        identity = "a" * 64
        cache = RUNNER.bundled_cache_directory(home / "config", identity)
        self.assertEqual(cache, home / "cache/numba" / identity[:24])
        self.assertNotIn("accounts", cache.parts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
