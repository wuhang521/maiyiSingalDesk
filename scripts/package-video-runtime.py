"""Build a clean, relocatable Windows x64 video sidecar (no private data).

python -B scripts/package-video-runtime.py
python -B scripts/package-video-runtime.py --verify-only

The build interpreter supplies pip only. The shipped interpreter is the pinned
official CPython embeddable distribution. Downloads use official HTTPS sources;
no installed/user site-packages, private configuration or media are copied.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
OUTPUT = ARTIFACTS / "bundled-video-runtime"
CACHE = ARTIFACTS / "video-runtime-build-cache"
PYTHON_VERSION = "3.12.10"
ARCHIVE = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{ARCHIVE}"
PYTHON_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
REQUIREMENTS = ROOT / "scripts/requirements-video-runtime.txt"
PYTHON_PATHS = "python312.zip\n.\nLib/site-packages\n"
FFMPEG_LICENSE_SHA256 = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
FFMPEG_README_SHA256 = "cf9643d68f4149f534cf5e010760cb5125b109c1ec04cfdab4fd38e968212e69"
FFMPEG_BINARY_SHA256 = "2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3"
FFMPEG_ARCHIVE_URL = "https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-essentials_build.7z"
FFMPEG_ARCHIVE_SHA256 = "4d903da24c1c07ee72e08a38689327ab0593da9d8802beae2fc50868c0c04cfe"
SKILLS = ("oldx-avatar", "video-production", "video-prompt-director")
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", "tests", "test", "bin", "Scripts"}
PRIVATE_NAMES = {"api_key", "credentials.json", "auth-session.json", "local-login.json", "video-config.json", "trial.db", "running.json"}
MEDIA_SUFFIXES = {".mp4", ".mov", ".webm", ".mp3", ".wav", ".flac", ".m4a", ".avi"}


def digest(target: Path) -> str:
    result = hashlib.sha256()
    with target.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def plain(target: Path) -> None:
    for part in (target.absolute(), *target.absolute().parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("Linked build/output paths are not allowed")


def member(value: str) -> PurePosixPath:
    if not isinstance(value, str) or any(char in value for char in ("\\", ":", "\x00")):
        raise ValueError("Invalid runtime member")
    result = PurePosixPath(value)
    if not result.parts or result.is_absolute() or str(result) != value or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ValueError("Invalid runtime member")
    return result


def allowed(target: Path) -> None:
    name = target.name.lower()
    # certifi's public root-CA bundle is required for provider HTTPS verification.
    if target.suffix.lower() == ".pem" and name == "cacert.pem" and target.parent.name == "certifi":
        if b"PRIVATE KEY" in target.read_bytes():
            raise ValueError("Private key found in certificate bundle")
        return
    if name.startswith(".env") or name in PRIVATE_NAMES or target.suffix.lower() in {".dpapi", ".key", ".pem", ".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".pyo"} | MEDIA_SUFFIXES:
        raise ValueError(f"Private/cache/media member is not distributable: {target.name}")


def copy_sources(work: Path) -> None:
    for source in sorted((ROOT / "server/app").rglob("*.py")):
        if any(part in EXCLUDED_DIRS for part in source.relative_to(ROOT).parts):
            continue
        plain(source)
        destination = work / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for name in ("run_video_desktop.py", "run_video_local.py"):
        source = ROOT / "server/scripts" / name
        plain(source)
        destination = work / "server/scripts" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for skill in SKILLS:
        folder = ROOT / "hermes-skill" / skill
        if not (folder / "SKILL.md").is_file():
            raise ValueError(f"Required Skill is missing: {skill}")
        for source in sorted(folder.rglob("*")):
            if any(part in EXCLUDED_DIRS for part in source.relative_to(folder).parts) or source.is_dir():
                continue
            plain(source)
            allowed(source)
            if source.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml", ".txt", ".toml"}:
                raise ValueError(f"Unexpected Skill asset: {source.name}")
            destination = work / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)


def ffmpeg_notice(binary: str) -> dict:
    return {"binary": binary, "distribution": "imageio-ffmpeg 0.6.0 / Gyan 7.1 essentials static Windows x64",
        "license": "GPL-3.0-or-later", "license_file": "licenses/ffmpeg-7.1/COPYING.GPLv3",
        "build_readme": "licenses/ffmpeg-7.1/README.txt", "source_record": "licenses/ffmpeg-7.1/SOURCE.json",
        "source": "https://github.com/FFmpeg/FFmpeg/commit/b08d7969c5"}


def copy_licenses(work: Path) -> None:
    folder = work / "licenses/ffmpeg-7.1"
    folder.mkdir(parents=True, exist_ok=True)
    for name, sha in (("COPYING.GPLv3", FFMPEG_LICENSE_SHA256), ("README.txt", FFMPEG_README_SHA256)):
        source = ROOT / "scripts/licenses/ffmpeg-7.1" / name
        plain(source)
        if not source.is_file() or digest(source) != sha:
            raise ValueError(f"Pinned FFmpeg distribution notice is missing or changed: {name}")
        shutil.copyfile(source, folder / name)
    runtime = json.loads((work / "runtime.json").read_text("utf-8"))
    if digest(work / str(member(runtime["ffmpeg"]))) != FFMPEG_BINARY_SHA256:
        raise ValueError("FFmpeg binary differs from its pinned Gyan 7.1 notices")
    record = {"version": "7.1-essentials_build-www.gyan.dev", "license": "GPL-3.0-or-later",
        "source_commit": "b08d7969c5", "source_url": "https://github.com/FFmpeg/FFmpeg/commit/b08d7969c5",
        "source_archive_url": "https://github.com/FFmpeg/FFmpeg/archive/b08d7969c5.tar.gz",
        "distributor_release": "https://github.com/GyanD/codexffmpeg/releases/tag/7.1",
        "distributor_archive_url": FFMPEG_ARCHIVE_URL, "distributor_archive_sha256": FFMPEG_ARCHIVE_SHA256,
        "binary_sha256": FFMPEG_BINARY_SHA256, "binary_matches_distributor_archive": True,
        "gpl_text_source": "https://raw.githubusercontent.com/FFmpeg/FFmpeg/b08d7969c5/COPYING.GPLv3",
        "gpl_text_sha256": FFMPEG_LICENSE_SHA256, "distributor_readme_sha256": FFMPEG_README_SHA256,
        "compiler": "gcc 14.2.0 (Rev1, Built by MSYS2 project)",
        "configuration_and_external_library_versions": "README.txt (unchanged distributor document)",
        "wrapper_distribution": "https://github.com/imageio/imageio-ffmpeg/releases/tag/v0.6.0"}
    (folder / "SOURCE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (folder / "NOTICE.txt").write_text(
        "FFmpeg 7.1 essentials build from Gyan Doshi, redistributed via imageio-ffmpeg 0.6.0.\n"
        "Copyright (c) 2000-2024 the FFmpeg developers.\n"
        "This separate static executable is licensed under GPL version 3 or later.\n"
        "COPYING.GPLv3 is the full license, not the BSD license of the Python wrapper.\n"
        "README.txt is the original Gyan 7.1 document: exact FFmpeg source, build configuration,\n"
        "external library versions and their build configuration. SOURCE.json pins the archive,\n"
        "binary, README and license hashes and records the matching upstream source location.\n"
        "The application communicates with FFmpeg as a separate process.\n", encoding="utf-8")


def clean_dependencies(site: Path) -> None:
    # Installed command launchers contain build-machine paths and are not used.
    plain(site)
    for directory, directories, files in os.walk(site, followlinks=False):
        current = Path(directory)
        for name in list(directories):
            folder = current / name
            if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
                raise ValueError("Linked dependency directory")
            if name in EXCLUDED_DIRS:
                shutil.rmtree(folder)
                directories.remove(name)
        for name in files:
            target = current / name
            if target.is_symlink():
                raise ValueError("Linked dependency file")
            if target.suffix.lower() in {".pyc", ".pyo"}:
                target.unlink()


def rows(directory: Path) -> list[dict]:
    plain(directory)
    result = []
    for current, directories, files in os.walk(directory, followlinks=False):
        parent = Path(current)
        for name in directories:
            target = parent / name
            if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
                raise ValueError("Linked runtime directory")
        for name in files:
            target = parent / name
            if target.is_symlink():
                raise ValueError("Linked runtime member")
            relative = target.relative_to(directory).as_posix()
            if relative == "manifest.json":
                continue
            allowed(target)
            result.append({"path": relative, "bytes": target.stat().st_size, "sha256": digest(target)})
    return sorted(result, key=lambda row: row["path"].casefold())


def verify(directory: Path) -> dict:
    plain(directory)
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    if manifest.get("schema") != 2 or manifest.get("kind") != "maiyi-bundled-video-runtime" or manifest.get("python_external") is not False:
        raise ValueError("Unrecognized bundled video runtime")
    expected = manifest.get("files")
    if not isinstance(expected, list) or not expected:
        raise ValueError("Missing runtime file inventory")
    names = set()
    expected_by_name = {}
    for row in expected:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise ValueError("Invalid bundled runtime inventory row")
        relative = str(member(row["path"]))
        if relative in names:
            raise ValueError("Duplicate bundled runtime inventory row")
        names.add(relative)
        expected_by_name[relative] = row
    if {row["path"]: row for row in rows(directory)} != expected_by_name:
        raise ValueError("Unexpected or modified bundled runtime files")
    runtime = json.loads((directory / "runtime.json").read_text("utf-8"))
    if (runtime.get("schema"), runtime.get("platform"), runtime.get("arch")) != (2, "win32", "x64"):
        raise ValueError("Unsupported bundled runtime identity")
    for name in ("python", "ffmpeg"):
        if str(member(runtime[name])) not in names:
            raise ValueError("Missing bundled executable")
    return {"runtime": str(directory), "files_verified": len(expected), "bytes": sum(row["bytes"] for row in expected),
            "manifest_sha256": digest(directory / "manifest.json"), "python_external": False, "credentials_included": False}


def refresh_sources(directory: Path) -> dict:
    """Refresh reviewed code after a build without downloading dependencies again."""
    verify(directory)
    if not directory.is_relative_to(ARTIFACTS.absolute()) or directory == ARTIFACTS.absolute():
        raise ValueError("Source refresh must remain inside this project's artifacts")
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    if manifest.get("requirements_sha256") != digest(REQUIREMENTS):
        raise ValueError("Runtime requirements changed; perform a clean build")
    managed = ("server/app/", "server/scripts/", "licenses/ffmpeg-7.1/", *(f"hermes-skill/{name}/" for name in SKILLS))
    for row in manifest["files"]:
        if row["path"].startswith(managed):
            (directory / str(member(row["path"]))).unlink()
    copy_sources(directory)
    copy_licenses(directory)
    (directory / "python/python312._pth").write_text(PYTHON_PATHS, encoding="utf-8")
    notices = json.loads((directory / "third-party-notices.json").read_text("utf-8"))
    notices["ffmpeg"] = ffmpeg_notice(json.loads((directory / "runtime.json").read_text("utf-8"))["ffmpeg"])
    (directory / "third-party-notices.json").write_text(json.dumps(notices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["files"] = rows(directory)
    manifest["total_bytes"] = sum(row["bytes"] for row in manifest["files"])
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verify(directory)


def build(args) -> dict:
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2 ** 32:
        raise ValueError("Build with CPython 3.12 Windows x64")
    archive = CACHE / ARCHIVE
    plain(archive)
    CACHE.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        with urllib.request.urlopen(PYTHON_URL, timeout=90) as response:
            data = response.read(32 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != PYTHON_SHA256:
            raise ValueError("Official Python archive hash mismatch")
        archive.write_bytes(data)
    if digest(archive) != PYTHON_SHA256:
        raise ValueError("Cached Python archive hash mismatch")
    output = args.output.absolute()
    plain(output)
    if not output.is_relative_to(ARTIFACTS.absolute()) or output == ARTIFACTS.absolute():
        raise ValueError("Build output must be inside this project's artifacts directory")
    work = output.with_name(output.name + ".building-" + uuid.uuid4().hex[:8])
    work.mkdir(parents=True)
    python_root = work / "python"
    python_root.mkdir()
    with zipfile.ZipFile(archive) as packed:
        for item in packed.infolist():
            member(item.filename.rstrip("/"))
            if item.file_size > 100 * 1024 * 1024:
                raise ValueError("Unexpected Python archive member size")
        packed.extractall(python_root)
    site = python_root / "Lib/site-packages"
    site.mkdir(parents=True)
    (python_root / "python312._pth").write_text(PYTHON_PATHS, encoding="utf-8")
    pip_report = CACHE / ("pip-install-" + uuid.uuid4().hex[:8] + ".json")
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "PROGRAMFILES", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PIP_CONFIG_FILE=os.devnull)
    command = [sys.executable, "-B", "-m", "pip", "--isolated", "--disable-pip-version-check", "install", "--target", str(site),
               "--ignore-installed", "--no-compile", "--no-warn-script-location", "--no-cache-dir", "--only-binary=:all:", "--no-binary=jieba",
               "--index-url", "https://pypi.org/simple", "--report", str(pip_report), "-r", str(REQUIREMENTS)]
    subprocess.run(command, env=environment, check=True, timeout=1800)
    clean_dependencies(site)
    copy_sources(work)
    ffmpeg = sorted((site / "imageio_ffmpeg/binaries").glob("ffmpeg-win-x86_64*.exe"))
    if len(ffmpeg) != 1:
        raise ValueError("Expected one imageio-ffmpeg Windows x64 binary")
    runtime = {"schema": 2, "platform": "win32", "arch": "x64", "python_version": PYTHON_VERSION,
               "python": "python/python.exe", "ffmpeg": ffmpeg[0].relative_to(work).as_posix()}
    (work / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    copy_licenses(work)
    report = json.loads(pip_report.read_text("utf-8"))
    packages = []
    for item in report["install"]:
        metadata = item["metadata"]
        download = item["download_info"]
        if not download["url"].startswith("https://files.pythonhosted.org/"):
            raise ValueError("Dependency did not come from official PyPI artifacts")
        packages.append({"name": metadata["name"], "version": metadata["version"],
                         "url": download["url"], "sha256": download["archive_info"]["hashes"]["sha256"],
                         "license": metadata.get("license_expression") or metadata.get("license", "See package license files")})
    packages.sort(key=lambda item: item["name"].lower())
    notices = {"python": {"version": PYTHON_VERSION, "url": PYTHON_URL, "sha256": PYTHON_SHA256,
                          "license_file": "python/LICENSE.txt"}, "packages": packages,
               "ffmpeg": ffmpeg_notice(runtime["ffmpeg"])}
    (work / "third-party-notices.json").write_text(json.dumps(notices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    listing = rows(work)
    manifest = {"schema": 2, "kind": "maiyi-bundled-video-runtime", "created_at": datetime.now(timezone.utc).isoformat(),
                "platform": "win32", "arch": "x64", "python_version": PYTHON_VERSION, "python_external": False,
                "credentials_included": False, "requirements_sha256": digest(REQUIREMENTS), "python_archive_sha256": PYTHON_SHA256,
                "packages": [{"name": item["name"], "version": item["version"]} for item in packages],
                "total_bytes": sum(row["bytes"] for row in listing), "files": listing}
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verified = verify(work)
    if output.exists():
        verify(output)
        output.rename(output.with_name(output.name + ".previous-" + uuid.uuid4().hex[:8]))
    try:
        work.rename(output)
    except PermissionError:
        # Some managed Windows workspaces deny directory rename. Do not relax
        # ACLs or delete evidence; a complete checked copy is safe to consume.
        if output.exists():
            raise
        shutil.copytree(work, output)
        verify(output)
    return {**verified, "runtime": str(output), "packages": len(packages), "python": PYTHON_VERSION,
            "mebibytes": round(verified["bytes"] / 1024 / 1024, 2)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--refresh-sources", action="store_true", help="Update only reviewed server/Skill code in a verified existing bundle")
    args = parser.parse_args(argv)
    try:
        if args.verify_only and args.refresh_sources:
            parser.error("--verify-only and --refresh-sources are mutually exclusive")
        result = refresh_sources(args.output.absolute()) if args.refresh_sources else verify(args.output.absolute()) if args.verify_only else build(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
