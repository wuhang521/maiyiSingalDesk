"""Check or explicitly install video resources into the existing 迈医协同 app.

Default is read-only. --install backs up managed resources before replacement;
--report may separately save a sanitized check result. User data is never deleted.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("D:/maiyi/outputs/maiyi-video-integrated-20261022")
TARGET = Path("D:/maiyi/signaldesk-desktop")
USER_DATA = Path(os.environ.get("APPDATA", "C:/Users/86173/AppData/Roaming")) / "迈医协同"
CONFIG_NAMES = ("video-config.json", "hermes-provider-config.dpapi", "hermes-voice-config.dpapi", "hermes-scene-config.dpapi")
MAGIC = {
    "hermes-provider-config.dpapi": b"HERMES_VIDEO_PRIMARY_DPAPI_V1\0",
    "hermes-voice-config.dpapi": b"HERMES_VIDEO_VOICE_DPAPI_V1\0",
    "hermes-scene-config.dpapi": b"HERMES_VIDEO_SCENE_DPAPI_V1\0",
}
CONFIG_FIELDS = set("prompt_api_key prompt_base_url prompt_model seedance_api_key seedance_base_url seedance_model TASK_VIDEO_PAID_ENABLED TASK_VIDEO_PYTHON TASK_VIDEO_FFMPEG TASK_VIDEO_PRICING_PROFILE TASK_VIDEO_SCENE_PROVIDER TASK_VIDEO_COST_REVIEW_REQUIRED TASK_VIDEO_VOICE_COST_PER_1000_CHARS TASK_VIDEO_SCENE_COST_PER_SECOND TASK_VIDEO_AVATAR_COST_PER_SECOND TASK_VIDEO_RUNNINGHUB_KEY_TYPE TASK_VIDEO_AVATAR_CURRENCY DASHSCOPE_API_KEY BL_KEY_FILE RUNNINGHUB_API_KEY RH_KEY_FILE".split())
REQUIRED = {"app.asar", "video-runtime/runtime.json", "video-runtime/server/app/main.py", "video-runtime/server/scripts/run_video_desktop.py", "video-runtime/server/scripts/run_video_local.py", "video-runtime/hermes-skill/oldx-avatar/SKILL.md"}
ELECTRON_FILES = ("chrome_100_percent.pak", "chrome_200_percent.pak", "d3dcompiler_47.dll", "dxcompiler.dll", "dxil.dll", "ffmpeg.dll", "icudtl.dat", "libEGL.dll", "libGLESv2.dll", "resources.pak", "snapshot_blob.bin", "v8_context_snapshot.bin", "vk_swiftshader.dll", "vulkan-1.dll")


class InstallError(RuntimeError):
    pass


def plain(target: Path) -> None:
    """Reject links before resolve() can hide them, including Windows junctions."""
    for part in (target.absolute(), *target.absolute().parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise InstallError("Refusing linked installation, stage or configuration paths")


def beneath(parent: Path, child: Path) -> bool:
    return child.absolute() != parent.absolute() and child.absolute().is_relative_to(parent.absolute())


def digest(target: Path) -> str:
    result = hashlib.sha256()
    with target.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_json(target: Path, label: str, limit: int = 2_000_000):
    plain(target)
    try:
        if not target.is_file() or target.stat().st_size > limit:
            raise ValueError()
        return json.loads(target.read_text("utf-8-sig"))
    except (ValueError, OSError, UnicodeError):
        raise InstallError(f"{label} is unavailable or invalid") from None


def member_path(value: str) -> Path:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise InstallError("Invalid manifest member")
    member = PurePosixPath(value)
    if member.is_absolute() or not member.parts or any(p in {"", ".", ".."} for p in member.parts) or str(member) != value:
        raise InstallError("Invalid manifest member")
    name = member.name.lower()
    if name.startswith(".env") or name in CONFIG_NAMES or name in {"local-login.json", "auth-session.json", "trial.db", "running.json", "credentials.json"} or member.suffix.lower() in {".dpapi", ".db", ".sqlite", ".sqlite3", ".key", ".pem", ".pyc", ".log"} or "__pycache__" in member.parts:
        raise InstallError("Private/runtime data is not allowed in resources")
    return Path(*member.parts)


def file_rows(directory: Path) -> list[dict]:
    result = []
    plain(directory)
    for item in sorted(directory.rglob("*")):
        plain(item)
        if item.is_file():
            result.append({"path": item.relative_to(directory).as_posix(), "bytes": item.stat().st_size, "sha256": digest(item)})
        elif not item.is_dir():
            raise InstallError("Resources contain a non-file member")
    return result


def verify_rows(directory: Path, rows: list[dict], *, manifest: bool = False) -> None:
    if not isinstance(rows, list) or not rows:
        raise InstallError("Resource manifest has no files")
    expected = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise InstallError("Invalid resource file record")
        member = member_path(row["path"])
        if row["path"] in expected or type(row["bytes"]) is not int or row["bytes"] < 0 or not isinstance(row["sha256"], str) or len(row["sha256"]) != 64:
            raise InstallError("Invalid or duplicate resource file record")
        expected.add(row["path"])
        target = directory / member
        plain(target)
        if not target.is_file() or target.stat().st_size != row["bytes"] or digest(target) != row["sha256"]:
            raise InstallError("Resource content differs from its manifest")
    actual = {row["path"] for row in file_rows(directory)}
    if manifest:
        actual.discard("manifest.json")
    if actual != expected:
        raise InstallError("Resources contain unexpected or missing files")


def asar_package(archive: Path) -> dict:
    """Read only package.json from an ASAR header; no extraction or execution."""
    try:
        with archive.open("rb") as stream:
            first, header_size, _, json_size = struct.unpack("<IIII", stream.read(16))
            if first != 4 or not 8 <= header_size <= 32_000_000 or not 0 < json_size <= header_size - 8:
                raise ValueError()
            header = json.loads(stream.read(json_size))
            record = header["files"]["package.json"]
            if record.get("unpacked") or "link" in record or not 0 < record["size"] <= 65536:
                raise ValueError()
            stream.seek(8 + header_size + int(record["offset"]))
            result = json.loads(stream.read(record["size"]))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, OSError, KeyError, TypeError, struct.error):
        raise InstallError("Application archive metadata is invalid") from None


def verify_stage(stage: Path) -> dict:
    plain(stage)
    manifest = read_json(stage / "manifest.json", "Stage manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("kind") != "maiyi-video-integrated-desktop" or manifest.get("app_name") != "signaldesk-desktop" or manifest.get("app_version") != "0.5.3" or manifest.get("credentials_included") is not False:
        raise InstallError("Unsupported desktop stage identity")
    verify_rows(stage, manifest.get("files"), manifest=True)
    names = {row["path"] for row in manifest["files"]}
    if not REQUIRED <= names or any(name != "app.asar" and not name.startswith("video-runtime/") for name in names):
        raise InstallError("Desktop stage is incomplete")
    runtime = read_json(stage / "video-runtime/runtime.json", "External Python runtime", 4096)
    if not isinstance(runtime, dict) or set(runtime) != {"schema", "python"} or runtime["schema"] != 1 or not isinstance(runtime["python"], str) or not Path(runtime["python"]).is_absolute():
        raise InstallError("External Python runtime settings are invalid")
    plain(Path(runtime["python"]))
    if not Path(runtime["python"]).is_file():
        raise InstallError("Dedicated external Python is unavailable")
    package = asar_package(stage / "app.asar")
    if (package.get("name"), package.get("version"), package.get("main")) != ("signaldesk-desktop", "0.5.3", "electron/main.cjs"):
        raise InstallError("Application identity must preserve the existing entry")
    return manifest


def windows_file_version(executable: Path) -> str:
    if os.name != "nt":
        raise InstallError("Installation checks require Windows")
    library = ctypes.WinDLL("version", use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    library.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    library.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    library.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = library.GetFileVersionInfoSizeW(str(executable), None)
    if not size:
        raise InstallError("Installed executable version is unavailable")
    data = ctypes.create_string_buffer(size)
    if not library.GetFileVersionInfoW(str(executable), 0, size, data):
        raise InstallError("Installed executable version is unavailable")
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not library.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 24:
        raise InstallError("Installed executable version is unavailable")
    fields = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
    return ".".join(str(value) for value in (fields[2] >> 16, fields[2] & 65535, fields[3] >> 16, fields[3] & 65535))


def installed_processes() -> list[dict]:
    """Inspect only executable identity; never process command lines or tokens."""
    if os.name != "nt":
        raise InstallError("Process checks require Windows")
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise InstallError("Unable to verify installed application processes")
    found = []
    try:
        entry = ProcessEntry(); entry.dwSize = ctypes.sizeof(entry)
        success = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            if entry.szExeFile.casefold() == "迈医协同.exe".casefold():
                process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                try:
                    buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                    known = bool(process and kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)))
                    if not known or Path(buffer.value).parent.absolute() == TARGET.absolute():
                        found.append({"pid": entry.th32ProcessID, "path_verified": known})
                finally:
                    if process: kernel.CloseHandle(process)
            success = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return found


def check_configuration(directory: Path) -> None:
    plain(directory)
    configuration = read_json(directory / "video-config.json", "Private video configuration", 65536)
    if not isinstance(configuration, dict) or set(configuration) - CONFIG_FIELDS or any(not isinstance(value, str) for value in configuration.values()):
        raise InstallError("Private video configuration has unsupported fields")
    if configuration.get("TASK_VIDEO_SCENE_PROVIDER") != "seedance" or configuration.get("TASK_VIDEO_COST_REVIEW_REQUIRED", "1").lower() not in {"0", "false", "no"} or configuration.get("TASK_VIDEO_PAID_ENABLED", "0").lower() not in {"1", "true", "yes"}:
        raise InstallError("Private configuration is not compatible with the reviewed direct Seedance / RunningHub avatar mode; existing settings were preserved")
    for name, magic in MAGIC.items():
        target = directory / name
        plain(target)
        if not target.is_file() or not len(magic) < target.stat().st_size <= 65536:
            raise InstallError("Required current-user encrypted model snapshot is unavailable")
        with target.open("rb") as stream:
            if stream.read(len(magic)) != magic:
                raise InstallError("Encrypted model snapshot format is incompatible")


def configuration_plan() -> tuple[Path, Path, bool]:
    destination = USER_DATA / "video-production/config"
    source = ROOT / "artifacts/video-local-trial"
    plain(destination); plain(source)
    existing = destination.exists()
    check_configuration(destination if existing else source)
    return source, destination, existing


def preflight(stage: Path) -> tuple[dict, dict]:
    manifest = verify_stage(stage)
    plain(TARGET); plain(USER_DATA)
    resources = TARGET / "resources"
    executable = TARGET / "迈医协同.exe"
    version = windows_file_version(executable)
    if version.split(".")[:3] != ["0", "5", "3"]:
        raise InstallError("Installed application version differs; a full reviewed installer is required")
    old_package = asar_package(resources / "app.asar")
    if (old_package.get("name"), old_package.get("version")) != ("signaldesk-desktop", "0.5.3"):
        raise InstallError("Installed archive identity differs")
    compatibility = manifest.get("compatibility")
    if not isinstance(compatibility, list) or len(compatibility) != len(ELECTRON_FILES) or {row.get("path") for row in compatibility if isinstance(row, dict)} != set(ELECTRON_FILES):
        raise InstallError("Missing Electron compatibility hashes")
    comparisons = []
    for record in compatibility:
        relative = member_path(record.get("path"))
        if len(relative.parts) != 1:
            raise InstallError("Invalid Electron compatibility member")
        member = TARGET / relative
        plain(member)
        actual = digest(member) if member.is_file() else None
        comparisons.append({"path": relative.as_posix(), "missing": actual is None, "sha256": actual, "matches": actual == record.get("sha256")})
    if not all(record["matches"] for record in comparisons):
        raise InstallError("Installed Electron resources differ from the staged runtime; no resources changed")
    source, destination, existing = configuration_plan()
    processes = installed_processes()
    report = {"schema": 1, "mode": "dry-run", "target": str(TARGET), "stage": str(stage), "user_data": str(USER_DATA), "installed_exe_version": version, "electron_version": manifest["electron_version"], "installed_app_asar_sha256": digest(resources / "app.asar"), "staged_app_asar_sha256": digest(stage / "app.asar"), "electron_resources": comparisons, "video_runtime_present": (resources / "video-runtime").exists(), "video_runtime_hashes": file_rows(resources / "video-runtime") if (resources / "video-runtime").exists() else [], "private_configuration": {"existing_preserved": existing, "would_import_files": [] if existing else list(CONFIG_NAMES), "destination": str(destination), "ciphertext_envelope_verified": True, "decrypted": False}, "running_installed_processes": processes, "ready_to_install": not processes, "original_shortcut_target": str(executable), "shortcuts_changed": False, "user_data_deleted": False, "server_deployment": False}
    return manifest, report


def write_json(target: Path, value: dict) -> None:
    plain(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def import_configuration() -> bool:
    source, destination, existing = configuration_plan()
    if existing:
        return False
    pending = destination.with_name(f"config.pending-{uuid.uuid4().hex}")
    pending.mkdir(parents=True)
    for name in CONFIG_NAMES:
        shutil.copyfile(source / name, pending / name)
        os.chmod(pending / name, 0o600)
    check_configuration(pending)
    # Never merge into or replace an existing configuration directory.
    if destination.exists():
        raise InstallError("Private configuration appeared during installation; preserved both copies")
    pending.rename(destination)
    return True


def backup_resources() -> tuple[Path, dict]:
    resources = TARGET / "resources"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup = OUTPUT / "baseline" / stamp
    plain(backup)
    backup.mkdir(parents=True)
    shutil.copyfile(resources / "app.asar", backup / "app.asar")
    old_runtime = resources / "video-runtime"
    retired = resources / f"video-runtime.previous-{stamp}"
    metadata = {"schema": 1, "kind": "maiyi-desktop-resource-backup", "target": str(TARGET), "app_asar_sha256": digest(backup / "app.asar"), "runtime_present": old_runtime.exists(), "runtime_files": file_rows(old_runtime) if old_runtime.exists() else [], "retired_runtime": str(retired), "electron_files": [{"path": name, "sha256": digest(TARGET / name)} for name in ELECTRON_FILES], "configuration_backed_up": False, "configuration_modified_by_rollback": False}
    (backup / "SHA256SUMS.txt").write_text(metadata["app_asar_sha256"] + "  app.asar\n", encoding="ascii")
    write_json(backup / "backup.json", metadata)
    return backup, metadata


def replace_resources(asar_source: Path, runtime_source: Path | None, backup: Path, metadata: dict) -> None:
    resources = TARGET / "resources"
    new_asar = resources / f"app.asar.pending-{uuid.uuid4().hex}"
    new_runtime = resources / f"video-runtime.pending-{uuid.uuid4().hex}"
    current_runtime = resources / "video-runtime"
    retired = Path(metadata["retired_runtime"])
    shutil.copyfile(asar_source, new_asar)
    if digest(new_asar) != digest(asar_source):
        raise InstallError("Copied application archive hash mismatch")
    if runtime_source is not None:
        rows = file_rows(runtime_source)
        shutil.copytree(runtime_source, new_runtime)
        verify_rows(new_runtime, rows)
    if installed_processes():
        raise InstallError("Close the existing 迈医协同 app before installation; no process was killed")
    moved_old = False
    moved_new = False
    try:
        if current_runtime.exists():
            current_runtime.rename(retired); moved_old = True
        if runtime_source is not None:
            new_runtime.rename(current_runtime); moved_new = True
        os.replace(new_asar, resources / "app.asar")
        if moved_old:
            retired.rename(backup / "video-runtime")
    except OSError:
        # Preserve failed code as evidence, restore only managed code, never data.
        if moved_new and current_runtime.exists():
            current_runtime.rename(backup / "failed-video-runtime")
        old = backup / "video-runtime" if (backup / "video-runtime").exists() else retired
        if moved_old and old.exists():
            shutil.copytree(old, current_runtime)
        restore = resources / f"app.asar.restore-{uuid.uuid4().hex}"
        shutil.copyfile(backup / "app.asar", restore)
        os.replace(restore, resources / "app.asar")
        raise InstallError("Resource replacement failed; original resources restored from backup") from None


def rollback_plan(backup: Path) -> tuple[dict, Path | None]:
    plain(backup)
    if not beneath(OUTPUT / "baseline", backup):
        raise InstallError("Rollback must use a backup in the dedicated baseline directory")
    metadata = read_json(backup / "backup.json", "Resource backup")
    if metadata.get("schema") != 1 or metadata.get("kind") != "maiyi-desktop-resource-backup" or metadata.get("target") != str(TARGET) or digest(backup / "app.asar") != metadata.get("app_asar_sha256"):
        raise InstallError("Resource backup identity or hash mismatch")
    runtime = None
    if metadata.get("runtime_present"):
        runtime = backup / "video-runtime"
        if not runtime.exists():
            runtime = Path(metadata["retired_runtime"])
            if runtime.parent != TARGET / "resources" or not runtime.name.startswith("video-runtime.previous-"):
                raise InstallError("Invalid interrupted-install runtime path")
        verify_rows(runtime, metadata["runtime_files"])
    return metadata, runtime


def rollback_preflight(backup: Path) -> tuple[dict, Path | None]:
    metadata, runtime = rollback_plan(backup)
    plain(TARGET); plain(USER_DATA)
    version = windows_file_version(TARGET / "迈医协同.exe")
    if version.split(".")[:3] != ["0", "5", "3"]:
        raise InstallError("Installed executable differs from this backup")
    expected = metadata.get("electron_files")
    if not isinstance(expected, list) or len(expected) != len(ELECTRON_FILES) or {r.get("path") for r in expected if isinstance(r, dict)} != set(ELECTRON_FILES):
        raise InstallError("Backup has no complete Electron compatibility record")
    for row in expected:
        member = TARGET / member_path(row["path"])
        plain(member)
        if not member.is_file() or digest(member) != row.get("sha256"):
            raise InstallError("Installed Electron differs from this backup")
    processes = installed_processes()
    report = {"schema": 1, "mode": "dry-run", "target": str(TARGET), "rollback_source": str(backup), "installed_exe_version": version, "restored_app_asar_sha256": metadata["app_asar_sha256"], "running_installed_processes": processes, "ready_to_install": not processes, "private_configuration": {"unchanged": True}, "user_data_deleted": False, "shortcuts_changed": False, "server_deployment": False}
    return report, runtime


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = ROOT / "artifacts/video-installer-self-test"
        cls.workspace.mkdir(parents=True, exist_ok=True)

    def test_forbidden_and_traversal(self):
        for value in ("../x", "/x", "a/../x", "a\\x", "C:/x", "video-runtime/.env", "video-config.json", "x.dpapi", "x/__pycache__/a.py"):
            with self.subTest(value=value), self.assertRaises(InstallError): member_path(value)

    def test_hash_tamper_and_extras(self):
        with tempfile.TemporaryDirectory(dir=self.workspace) as value:
            folder = Path(value); (folder / "safe.py").write_text("print(1)")
            rows = file_rows(folder); verify_rows(folder, rows)
            (folder / "safe.py").write_text("print(2)")
            with self.assertRaises(InstallError): verify_rows(folder, rows)
            rows = file_rows(folder); (folder / "extra.py").write_text("")
            with self.assertRaises(InstallError): verify_rows(folder, rows)

    def test_duplicate_manifest(self):
        with tempfile.TemporaryDirectory(dir=self.workspace) as value:
            folder = Path(value); (folder / "safe.py").write_text("")
            row = file_rows(folder)[0]
            with self.assertRaises(InstallError): verify_rows(folder, [row, row])

    def test_private_configuration_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir=self.workspace) as value:
            folder = Path(value); config = {"TASK_VIDEO_SCENE_PROVIDER": "seedance", "TASK_VIDEO_COST_REVIEW_REQUIRED": "0", "TASK_VIDEO_PAID_ENABLED": "1"}
            (folder / "video-config.json").write_text(json.dumps(config))
            for name, magic in MAGIC.items(): (folder / name).write_bytes(magic + b"synthetic-ciphertext")
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            check_configuration(folder)
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
            (folder / "video-config.json").write_text(json.dumps({**config, "TASK_VIDEO_SCENE_PROVIDER": "runninghub"}))
            with self.assertRaises(InstallError): check_configuration(folder)

    def test_installed_asar_metadata_read_only(self):
        before = digest(TARGET / "resources/app.asar")
        package = asar_package(TARGET / "resources/app.asar")
        self.assertEqual((package["name"], package["version"]), ("signaldesk-desktop", "0.5.3"))
        self.assertEqual(before, digest(TARGET / "resources/app.asar"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=OUTPUT / "stage")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Explicit read-only default")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--rollback", type=Path, help="Inspect a backup; add --install to restore managed resources only")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return 0 if unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PreparationTests)).wasSuccessful() else 1
    if args.install and args.dry_run:
        parser.error("--install and --dry-run cannot be combined")
    try:
        stage = args.stage.absolute()
        if not beneath(OUTPUT, stage):
            raise InstallError("Stage must be inside the dedicated output directory")
        if args.report and not beneath(OUTPUT, args.report.absolute()):
            raise InstallError("Reports must be inside the dedicated output directory")
        rollback_runtime = None
        if args.rollback:
            report, rollback_runtime = rollback_preflight(args.rollback.absolute())
        else:
            _, report = preflight(stage)
        if args.install:
            if report["running_installed_processes"]:
                raise InstallError("Close the installed 迈医协同 app first; no process was killed")
            # Recheck source hashes immediately before backups and installation.
            if not args.rollback:
                verify_stage(stage)
            backup, metadata = backup_resources()
            if args.rollback:
                replace_resources(args.rollback.absolute() / "app.asar", rollback_runtime, backup, metadata)
                imported = False
            else:
                imported = import_configuration()
                replace_resources(stage / "app.asar", stage / "video-runtime", backup, metadata)
            report.update(mode="rolled-back" if args.rollback else "installed", backup=str(backup), configuration_imported=imported, installed_app_asar_sha256=digest(TARGET / "resources/app.asar"), rollback_command=f'"{sys.executable}" -B "{Path(__file__)}" --rollback "{backup}" --install')
        if args.report:
            write_json(args.report.absolute(), report)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (InstallError, OSError):
        # Never serialize configuration values, DPAPI bytes or raw exception bodies.
        message = str(sys.exception()) if isinstance(sys.exception(), InstallError) else "Filesystem check or resource operation failed; private values were not logged"
        failure = {"schema": 1, "mode": "failed", "error": message, "installation_requested": args.install, "no_process_killed": True}
        if args.report and beneath(OUTPUT, args.report.absolute()):
            try: write_json(args.report.absolute(), failure)
            except (InstallError, OSError): pass
        print(json.dumps(failure, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
