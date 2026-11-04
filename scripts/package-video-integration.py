"""Package the reviewed video integration files, never local credentials/data."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("D:/maiyi/outputs/hermes-runninghub-direct-20261022")
DOCS = Path("D:/maiyi/docs/delivery/hermes-video-integration")
FILES = [
    "server/app/main.py", "server/app/config.py", "server/app/video.py",
    "server/app/video_projects.py", "server/app/video_project_models.py", "server/app/video_runtime.py", "server/app/video_runninghub.py", "server/app/video_pricing.py", "server/app/video_hermes_config.py", "server/app/video_hermes_voice_config.py", "server/app/video_hermes_scene_config.py",
    "server/migrations/versions/20261019_01_video_projects.py",
    "server/scripts/run_video_local.py",
    "server/tests/test_video_api.py", "server/tests/test_video_projects.py", "server/tests/test_video_runtime.py", "server/tests/test_video_runninghub.py", "server/tests/test_video_skill_client.py", "server/tests/test_video_hermes_config.py", "server/tests/test_video_local_config.py", "server/tests/test_video_hermes_voice_config.py", "server/tests/test_video_voice_wiring.py", "server/tests/video_preview.py",
    "src/components/VideoGenerationWorkspace.tsx", "src/components/VideoGenerationWorkspace.css", "src/components/VideoGenerationWorkspace.test.tsx",
    "src/components/ContentGenerationView.tsx", "src/components/ContentGenerationView.test.tsx",
    "src/lib/videoApi.ts", "src/lib/videoApi.test.ts",
    "scripts/video-local.mjs", "scripts/video-desktop-smoke.mjs", "scripts/video-e2e.mjs", "scripts/import-hermes-video-config.py", "scripts/import-hermes-voice-config.py", "scripts/package-video-integration.py",
    "server/tests/test_video_hermes_scene_config.py", "scripts/import-hermes-scene-config.py",
    "server/tests/test_video_voice_billing.py", "server/tests/test_video_avatar_billing.py",
    "config/video-local.example.json", "hermes-skill/video-prompt-director/SKILL.md",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--date", default="2026-10-22")
    args = parser.parse_args()
    output_directory = args.output.resolve()
    selected = {ROOT / value: "source/" + value for value in FILES}
    for folder in ("hermes-skill/oldx-avatar", "hermes-skill/video-production"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                selected[path] = "source/" + path.relative_to(ROOT).as_posix()
    for path in DOCS.rglob("*"):
        if path.is_file() and path.name != "SOURCE-MANIFEST.json":
            selected[path] = "delivery/" + path.relative_to(DOCS).as_posix()
    selected[Path("D:/maiyi/启动Hermes视频版.cmd")] = "启动Hermes视频版.cmd"
    forbidden = {".env", "api_key", "credentials.json", "local-login.json", "video-config.json"}
    for path, member in selected.items():
        if path.name in forbidden or path.suffix.lower() in {".dpapi", ".db", ".key", ".pem", ".pyc"} or "artifacts/" in member:
            raise RuntimeError("Refusing credential/runtime data in package")
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Missing or linked package member")
    manifest = {"date": args.date, "kind": "selected-source-and-evidence", "source_root": str(ROOT),
        "not_standalone_installer": True, "credentials_included": False,
        "files": [{"member": member, "bytes": path.stat().st_size, "sha256": digest(path)} for path, member in sorted(selected.items(), key=lambda row: row[1])]}
    baseline = output_directory / "baseline"
    manifest["baseline_files"] = [{"path": p.relative_to(baseline).as_posix(), "sha256": digest(p)} for p in baseline.rglob("*") if p.is_file()]
    manifest_path = DOCS / "SOURCE-MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_directory.mkdir(parents=True, exist_ok=True)
    target = output_directory / "hermes-video-integration-source.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, member in sorted(selected.items(), key=lambda row: row[1]):
            archive.write(path, member)
        archive.write(manifest_path, "SOURCE-MANIFEST.json")
        archive.writestr("README.txt", "Hermes 本机视频集成：相关源码与验证证据。\n这是当前既有仓库的选定文件快照，不是独立安装器；main/config内保留原有用户改动，不要向其他版本盲目覆盖。\n最新功能、无报价操作与启用状态见 delivery/RUNNINGHUB-DIRECT-20261022.md；运行环境见 delivery/implementation.md。原安装版与服务器未被覆盖。\n本机模型密文、Key、数据库和登录文件不包含在包内；本机入口使用工作区现有运行环境。\n")
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Archive verification failed")
        for row in manifest["files"]:
            if hashlib.sha256(archive.read(row["member"])).hexdigest() != row["sha256"]:
                raise RuntimeError("Archive member hash mismatch")
    (output_directory / "package-sha256.txt").write_text(digest(target) + "  " + target.name + "\n", encoding="utf-8")
    print(json.dumps({"package": str(target), "members_verified": len(selected), "bytes": target.stat().st_size, "sha256": digest(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
