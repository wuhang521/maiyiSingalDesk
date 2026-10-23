"""Offline bundle inventory, traversal and private-data exclusion tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("bundle_builder", Path(__file__).with_name("package-video-runtime.py"))
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class PackageTests(unittest.TestCase):
    def setUp(self):
        parent = BUILDER.ARTIFACTS / "video-runtime-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="inventory-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_paths_cannot_escape_the_runtime(self):
        for value in ("../secret", "x/../y", "/x", "C:/x", "x\\y", "x//y", "./x", "x:stream"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                BUILDER.member(value)
        self.assertEqual(str(BUILDER.member("python/python.exe")), "python/python.exe")

    def test_private_configuration_cache_and_media_are_excluded(self):
        for name in (".env", ".env.production", "api_key", "credentials.json", "video-config.json", "trial.db", "session.dpapi", "key.pem", "generated.mp4", "voice.wav", "cache.pyc", "output.log"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                BUILDER.allowed(self.root / name)

    def test_public_ca_bundle_is_allowed_but_never_a_private_key(self):
        folder = self.root / "certifi"
        folder.mkdir()
        target = folder / "cacert.pem"
        target.write_bytes(b"-----BEGIN CERTIFICATE-----\npublic-ca\n-----END CERTIFICATE-----")
        BUILDER.allowed(target)
        target.write_bytes(b"-----BEGIN PRIVATE KEY-----\nsynthetic-placeholder")
        with self.assertRaises(ValueError):
            BUILDER.allowed(target)

    def inventory(self):
        for relative in ("python/python.exe", "tools/ffmpeg.exe"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"synthetic test binary")
        (self.root / "runtime.json").write_text(json.dumps({"schema": 2, "platform": "win32", "arch": "x64",
            "python": "python/python.exe", "ffmpeg": "tools/ffmpeg.exe"}))
        manifest = {"schema": 2, "kind": "maiyi-bundled-video-runtime", "python_external": False, "files": BUILDER.rows(self.root)}
        (self.root / "manifest.json").write_text(json.dumps(manifest))

    def test_tampered_file_is_rejected(self):
        self.inventory()
        self.assertEqual(BUILDER.verify(self.root)["files_verified"], 3)
        (self.root / "python/python.exe").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            BUILDER.verify(self.root)

    def test_unexpected_file_is_rejected(self):
        self.inventory()
        (self.root / "extra.txt").write_text("unexpected")
        with self.assertRaises(ValueError):
            BUILDER.verify(self.root)

    def test_duplicate_manifest_member_is_rejected(self):
        self.inventory()
        target = self.root / "manifest.json"
        manifest = json.loads(target.read_text())
        manifest["files"].append(dict(manifest["files"][0]))
        target.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            BUILDER.verify(self.root)

    def test_ffmpeg_notices_are_pinned_complete_distributor_documents(self):
        directory = BUILDER.ROOT / "scripts/licenses/ffmpeg-7.1"
        license_file = directory / "COPYING.GPLv3"
        readme = directory / "README.txt"
        self.assertEqual(BUILDER.digest(license_file), BUILDER.FFMPEG_LICENSE_SHA256)
        self.assertEqual(BUILDER.digest(readme), BUILDER.FFMPEG_README_SHA256)
        self.assertGreater(license_file.stat().st_size, 35000)
        self.assertIn("END OF TERMS AND CONDITIONS", license_file.read_text("utf-8"))
        self.assertIn("b08d7969c5", readme.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
