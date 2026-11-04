"""Prepare a new, whitelisted public source snapshot without Git or network access.

Default: scan first, then create a never-before-existing source-<uuid> directory.
Use --dry-run to inspect the selection and privacy findings without writing files.
No original .git history, private configuration, runtime state, or media is read.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid


VERSION = "0.6.0"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PARENT = Path("D:/maiyi/outputs/maiyi-release-0.6.0")
SKILLS = ("oldx-avatar", "video-production", "video-prompt-director")
REQUIRED_FILES = (
    "package.json", "package-lock.json", "index.html", "tsconfig.json", "vite.config.ts", ".gitignore", "build/icon.png",
    "server/requirements.txt", "server/alembic.ini", "server/scripts/run_video_desktop.py", "server/scripts/run_video_local.py",
    "scripts/package-video-runtime.py", "scripts/requirements-video-runtime.txt", "scripts/verify-bundled-video-runtime.py",
    "scripts/licenses/ffmpeg-7.1/COPYING.GPLv3", "scripts/licenses/ffmpeg-7.1/README.txt",
    "scripts/generate-icon.cjs", "scripts/generate-third-party-notices.mjs", "scripts/test-desktop-updater.cjs",
    "scripts/test-video-desktop.cjs", "scripts/test-update-transport.cjs", "scripts/test_video_runtime_bootstrap.py",
    "scripts/test_package_video_runtime.py", "scripts/package-smoke.mjs",
    "scripts/e2e.mjs", "scripts/content-e2e.mjs", "scripts/member-e2e.mjs", "scripts/knowledge-e2e.mjs",
    "scripts/import-hermes-scene-config.py", "scripts/import-hermes-video-config.py", "scripts/import-hermes-voice-config.py",
    ".github/workflows/desktop-release.yml", ".github/release-notes/v0.6.0.md",
)
SOURCE_TREES = ("src", "electron", "server/app", "server/migrations", "server/tests", *(f"hermes-skill/{name}" for name in SKILLS))
TEXT_SUFFIXES = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs", ".css", ".scss", ".html", ".json", ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".mako", ".svg"}
PUBLIC_IMAGE_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg", ".ico"}
EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".vite", "artifacts", "release", "dist", "coverage", "privateconfigs", "private-configs", "test-results", "playwright-report", "logs", "cache", "caches"}
PRIVATE_NAMES = {"api_key", "api-key", "credentials.json", "auth-session.json", "local-login.json", "video-config.json", "private-config.json", "trial.db", "running.json"}
EXCLUDED_MEMBERS = {"server/tests/video_preview.py"}
EXCLUDED_SUFFIXES = {".dpapi", ".key", ".pem", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".pyo", ".mp4", ".mov", ".webm", ".mp3", ".wav", ".flac", ".m4a", ".avi", ".exe", ".dll", ".zip", ".7z"}

# Fixed, visibly synthetic test/example literals only. No entropy-based exemptions.
PLACEHOLDERS = {
    "test-api-key", "test-task-key", "test-provider-key", "test-runninghub-key", "test-seedance-key", "test-prompt-key",
    "desktop-test-api-key", "desktop-test-token", "test-token-123", "another-test-token", "another-provider-key",
    "dummy-api-key", "dummy-secret", "example-api-key", "your-api-key", "your_api_key", "YOUR_API_KEY",
    "YOUR_RUNNINGHUB_API_KEY", "YOUR_DASHSCOPE_API_KEY", "replace-with-api-key", "replace-me", "change-me", "changeme",
    "REDACTED", "<redacted>", "<API_KEY>", "<YOUR_API_KEY>", "<your-api-key>", "<token>", "<key>",
    "sk-test-12345678901234567890", "sk-fake-key-for-testing-only", "hiddensecretvalue",
}
# Exact reviewed synthetic fixture lines, pinned by path and complete-line SHA256.
# Any edited line is scanned again; this never exempts an entire test file.
TEST_FIXTURE_LINE_HASHES = {'scripts/content-e2e.mjs': ['e2e3e35647c85ce63187ce7eb78cf282399834affb6adcb6621b80d454db3552'],
 'scripts/e2e.mjs': ['fa1076d2fa6186f6790a71f2800dcf446a665a78f9ce4642b4e58614f5b1063c'],
 'scripts/member-e2e.mjs': ['3d0cb7b0e80cfa3b36c1d0f417492718206c2751316e7bc6a553bef29fe67d53',
                            'bc8e3ca7baa5707c114d997e475eb0b147033f2dc1e1fbf32320052e1568fbd1'],
 'scripts/test-desktop-updater.cjs': ['c1c91ff0c74d284185029d7872826a5778bf17e38e45205d6591d95b8d9665f9'],
 'scripts/test-video-desktop.cjs': ['7ff2ed9aa90a1fd9f00a7e8cf1dde566e34f123305bdd927e489e03be3f58db4'],
 'scripts/test_package_video_runtime.py': ['c5c796df28011ded5946ced36f1e821fcf2bd270ba01f7c0b5c4ec6c826b508d'],
 'server/tests/test_api.py': ['02f4b40cab1eb2566fa92c4c576e3dd5e7a3330e485884bacef7063768c40d62',
                              '42fb4f25267356f3e4cfb5b29a161f4e23cbe4054a63d2f6609e0f929ae304d3',
                              '70ed4dbb266267100371d868d0db0ea192199555df3e9f8990afb3534637b8cf',
                              '83b51e925c28e774c42a26a2a5a3728cbfcf8ee66f498145375054486473e255',
                              'ba0d3083e8305b422d4f022444f97a5615aa5d9e7f8b70dd830e4aa518785b6d',
                              'baf295ed69903c5219d37c4365640171f48483f529a43352e4c62e04065340be'],
 'server/tests/test_knowledge_cloud.py': ['03c4e63d889ff0da4bd7ccc2aa8663fac44886fab3a23466dbc4725e28a7c35a'],
 'server/tests/test_members.py': ['0e92aeacc83655fa70a954c3a3c086813e0c82c63823668f2392ffb12bcfaedd',
                                  '150930a02f3215c74591dd86c313fe4ad417065bef27b275336c405113746ba7',
                                  '331826f860b1bed4174292af4c344e76a7f99ed69a8c1a32e7130a0a706de284',
                                  '3931013e7d72a0a6c4aae37f6638046055b6f78e56392c8a026d1bb62a955d12',
                                  '97d3692528d76a5a68d45fa753da95e4f4266adce2737480a69f15103edef0aa',
                                  '9c851da1244406b89185a7cc7a2ad3bd1881c2f40bdc143baa42b47117291f1c',
                                  '9d5dffebd10c4b40887c52e759c5fe92d9841a7c3021bd3f1a66e6a53eeb59c2',
                                  'c47e08b4f4f7759d1158943d300318dbb9ae2377011084b6fcac94e9a45a74eb',
                                  'e250ff216dd9ffb82adf7bd59a04ec32343af1ddbd306a439b0286d4ccbba48a',
                                  'ead107e2750453a4a06e39674039b965713fa47286c43d0ff09d9e6ae9311cd1',
                                  'f96f2f48750e3a3f26fee3ea3d2ceae2468f7cdd355ec26b58c54e8936cabdce'],
 'server/tests/test_video_avatar_billing.py': ['9a9770271feb3f8196c7cdf8f54208f7269e0be2dd8341e20a19982fa2107d43'],
 'server/tests/test_video_hermes_config.py': ['9a0b7812ec5517bdc9d22f83117d63016414a6453a17b60250c1f8c40367ea80',
                                              'f3bddd4f3dcecff08bf244c743125a3f1e73808c1f412692c2fda8ddc6371116'],
 'server/tests/test_video_hermes_scene_config.py': ['abbcdbf1b53fb159ab0f59ec2b86f0540883d3086684d1f20a76716ea341d3e7',
                                                    'f3302b4e1ff0c8c77fed45311fec3967ed0d281daa82e03f689ac370e88b3a4a'],
 'server/tests/test_video_hermes_voice_config.py': ['6d747c11c8d1ff7e5fe3f02471d13bbd4ead32024de5153a8b459c677b67f0ec',
                                                    '8b938453973ac17356d7aaf51f51594d02363d149b60bba60aa172a2f2cad96b',
                                                    'f3f66f3a9fe36bf157ac8678d81543dd64633b8351d3560da95125f6bd8acd93'],
 'server/tests/test_video_runninghub.py': ['fae8a141b0e65db2b4d2efdc524501995576fd232fba0b45ffee9c0ea75d07fe'],
 'server/tests/test_video_voice_wiring.py': ['0176f19104a4869b9ea240ee97bb00d59f7d71a91dc918e429edb41d199121de',
                                             '637d4409d41a07e33d710a8ed9543ca8f8e40d8550192c5a279493f6b55c94a0'],
 'src/App.test.tsx': ['ed00e779982463ca8d94fcd2af52a48c2b812e3d7370571fc082738c5f95ed5c'],
 'src/components/TaskWidget.test.tsx': ['80a3008b38ffd3fd145335b7b339b2ceaa8f3887a339d32bedcaabf8d94e6333'],
 'src/lib/articleApi.test.ts': ['ca6b1c53ac0b543a4047104051e7b44d230f03ae06fe8388b2d76c7fa3d84fb2'],
 'src/lib/authApi.test.ts': ['83d7e7c77ff23f010e35dba7e59ec0c024890edf3d55fc63781ef0e8b74d107f',
                             'dfe1d00ec3c1435130fc6996b1d108e511a7ff13c84ec0fbbe5b6e40b919fade',
                             'ed00e779982463ca8d94fcd2af52a48c2b812e3d7370571fc082738c5f95ed5c'],
 'src/lib/videoApi.test.ts': ['0130cf1aaa1368ae062073e0191a8179c3861917ab484c5cd35e3368afbb6d5a',
                              '19f5cabd2d15d390cb359842de216668a499392d7a5e7e3dfaf6b5b6a0e2607f',
                              '67fed7a58128af6d185ffa4456ba406abf45cb71bf6e897e59b9be1fa5516c13',
                              'a0efdc1e37e29debfbfff4b97d8e400c13ce3dc638538802347af862c12ddd00']}
TOKEN_RULES = (
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("provider-secret-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("jwt-literal", re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")),
)
CREDENTIAL_LITERAL = re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret(?:[_-]?key)?|password|passwd)\b[\"']?\s*[:=]\s*[\"']([^\"'\r\n]{12,})[\"']")
BEARER_LITERAL = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._-]{20,})")
HEX_CREDENTIAL = re.compile(r"(?i)\b[A-Za-z_]*(?:key|token|secret)[A-Za-z_]*\b[\"']?\s*[:=]\s*[\"']([a-f0-9]{32})[\"']")
USER_PROFILE = re.compile(r"(?i)\bC:(?:[/\\]|\\\\)Users(?:[/\\]|\\\\)(?!Public\b|Default\b|<|\$|\{)[A-Za-z0-9_.-]+")


def plain(target: Path) -> None:
    for candidate in (target.absolute(), *target.absolute().parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("Linked source/output paths are not allowed")
        attributes = getattr(candidate.stat(), "st_file_attributes", 0) if candidate.exists() else 0
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("Reparse-point source/output paths are not allowed")


def relative_name(target: Path, root: Path) -> str:
    value = target.relative_to(root).as_posix()
    if value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Invalid source member")
    return value


def is_private(relative: str) -> bool:
    parts = [part.lower() for part in relative.split("/")]
    name = parts[-1]
    return relative in EXCLUDED_MEMBERS or any(part in EXCLUDED_DIRS for part in parts) or name.startswith(".env") or name in PRIVATE_NAMES or Path(name).suffix in EXCLUDED_SUFFIXES


def is_public_image(relative: str) -> bool:
    return relative == "build/icon.png" or relative.startswith("src/assets/") and Path(relative).suffix.lower() in PUBLIC_IMAGE_SUFFIXES


def select_sources(root: Path) -> tuple[list[Path], list[str]]:
    plain(root)
    selected: dict[str, Path] = {}
    excluded = []
    for relative in REQUIRED_FILES:
        target = root / relative
        plain(target)
        if not target.is_file():
            raise ValueError(f"Required public source file is missing: {relative}")
        selected[relative] = target
    package = json.loads((root / "package.json").read_text(encoding="utf-8-sig"))
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8-sig"))
    if package.get("version") != VERSION or lock.get("version") != VERSION or lock.get("packages", {}).get("", {}).get("version") != VERSION:
        raise ValueError("Public release package and lock versions must all match 0.6.0")
    for relative in SOURCE_TREES:
        base = root / relative
        plain(base)
        if not base.is_dir():
            raise ValueError(f"Required public source directory is missing: {relative}")
        for directory, subdirs, files in os.walk(base, followlinks=False):
            current = Path(directory)
            for name in list(subdirs):
                folder = current / name
                plain(folder)
                if name.lower() in EXCLUDED_DIRS or name.startswith(".env"):
                    excluded.append(relative_name(folder, root) + "/")
                    subdirs.remove(name)
            for name in files:
                target = current / name
                rel = relative_name(target, root)
                plain(target)
                if is_private(rel) or target.suffix.lower() not in TEXT_SUFFIXES and not is_public_image(rel):
                    excluded.append(rel)
                    continue
                selected[rel] = target
    for skill in SKILLS:
        if f"hermes-skill/{skill}/SKILL.md" not in selected:
            raise ValueError(f"Required public Skill is missing: {skill}")
    return [selected[name] for name in sorted(selected)], sorted(excluded)


def scan_text(relative: str, text: str) -> list[dict]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        if hashlib.sha256(line.encode("utf-8")).hexdigest() in TEST_FIXTURE_LINE_HASHES.get(relative, set()):
            continue
        for rule, pattern in TOKEN_RULES:
            for match in pattern.finditer(line):
                if match.group() not in PLACEHOLDERS:
                    findings.append({"file": relative, "line": number, "rule": rule})
        for match in CREDENTIAL_LITERAL.finditer(line):
            value = match.group(1)
            if value not in PLACEHOLDERS and not value.startswith(("${", "<", "{", "%")):
                findings.append({"file": relative, "line": number, "rule": "credential-literal"})
        for match in BEARER_LITERAL.finditer(line):
            if match.group(1) not in PLACEHOLDERS:
                findings.append({"file": relative, "line": number, "rule": "bearer-literal"})
        if HEX_CREDENTIAL.search(line):
            findings.append({"file": relative, "line": number, "rule": "hex-credential-literal"})
        if USER_PROFILE.search(line):
            findings.append({"file": relative, "line": number, "rule": "personal-windows-profile-path"})
    return list({(row["file"], row["line"], row["rule"]): row for row in findings}.values())


def scan_sources(files: list[Path], root: Path) -> list[dict]:
    findings = []
    for target in files:
        relative = relative_name(target, root)
        if is_public_image(relative):
            continue
        try:
            text = target.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            findings.append({"file": relative, "line": 1, "rule": "unexpected-binary-source"})
            continue
        findings.extend(scan_text(relative, text))
    return findings


def digest(target: Path) -> str:
    result = hashlib.sha256()
    with target.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


README = """# 迈医协同 0.6.0

本目录是为公共仓库准备的源码快照，未包含原项目 Git 历史、构建产物、账号资料或供应商凭据。发布前复核 SOURCE-MANIFEST.json 和 SHA256SUMS.txt；保留目标仓库已有的初始说明提交，以快进提交加入本快照。

## Windows 构建

使用 Windows x64、Node.js 22、Python 3.12 和 npm。依赖下载需要访问对应官方软件源。

```powershell
npm ci
npm run notices
npm run test:desktop
npm run dist:win
```

`dist:win` 执行类型检查、前端测试、前端构建、本机视频运行环境打包和 NSIS 安装包生成。安装包位于 `release/`。首次构建会下载固定版本的 Python 嵌入式运行环境与依赖；该运行环境跟随 Windows 安装包分发。

## 迁移与自动更新

旧版客户端只有手动检查功能，首次迁移到 0.6.0 需要下载新版安装包并覆盖安装。后续版本可检查并自动下载，在用户确认“安装并重启”后才安装。普通退出不自动安装；未保存编辑或活动视频任务会阻止安装。

覆盖安装时保留现有 AppData 用户目录，勿删除本地账户、登录、视频项目和配置。此源码快照不包含任何人的 AppData；安装器不应通过迁移清空用户资料。

各用户的业务后台登录、视频供应商密钥和相关凭据必须各自配置；不会把开发者凭据分发给其他用户。生产服务部署、医疗内容专业审核和平台发布权限需分别准备。

## 发布核对

公共仓库不导入旧项目历史。发布前递增 package.json 与 package-lock.json 版本，确保 tag 与版本一致；Windows 自动更新依赖 Release 中配套的安装包、latest.yml 和 blockmap。工作流可由推送对应 tag 构建并发布；已验证的本地安装包也可在完成审计后发布为同版本 Release。本地构建命令本身不会发布。

源文件白名单仅保留当前桌面端及其构建、测试、视频运行环境所需内容。原工作区的部署、恢复、临时验证脚本和其他平台工程未纳入本快照。npm 通用 E2E 别名及 Hermes 配置导入器源码已保留，以维持引用完整；历史 E2E 可能仍含旧版页面断言，不属于本次正式发布 CI 的验证结论，运行前应核对当前界面。
"""


def write_snapshot(root: Path, files: list[Path], output_parent: Path, excluded: list[str]) -> dict:
    plain(output_parent)
    output_parent.mkdir(parents=True, exist_ok=True)
    destination = output_parent / f"source-{uuid.uuid4().hex}"
    destination.mkdir(exist_ok=False)
    for source in files:
        relative = relative_name(source, root)
        target = destination / relative
        plain(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if digest(source) != digest(target):
            raise ValueError(f"Source changed while copying: {relative}")
    (destination / "README.md").write_text(README, encoding="utf-8", newline="\n")
    copied = sorted(target for target in destination.rglob("*") if target.is_file())
    findings = scan_sources(copied, destination)
    if findings:
        return {"status": "BLOCKED", "destination": str(destination), "findings": findings, "git_operations": 0, "network_operations": 0}
    rows = [{"path": relative_name(target, destination), "bytes": target.stat().st_size, "sha256": digest(target)} for target in copied]
    manifest = {"schema": 1, "kind": "maiyi-public-source-snapshot", "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(), "git_history_included": False,
        "private_configuration_included": False, "privacy_scan": {"status": "PASS", "findings": [], "scope": "whitelisted UTF-8 source; public raster branding assets excluded from text matching"},
        "file_count": len(rows), "files": rows, "excluded_member_count": len(excluded)}
    manifest_path = destination / "SOURCE-MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    sums = [f"{row['sha256']}  {row['path']}" for row in rows]
    sums.append(f"{digest(manifest_path)}  SOURCE-MANIFEST.json")
    (destination / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    return {"status": "PASS", "destination": str(destination), "source_file_count": len(files), "snapshot_file_count": len(rows) + 2,
        "manifest_sha256": digest(manifest_path), "findings": [], "git_operations": 0, "network_operations": 0}


def self_test() -> dict:
    checks = [
        bool(scan_text("example.py", 'api_key = "' + "a" * 32 + '"')),
        bool(scan_text("example.txt", "ghp_" + "A" * 36)),
        bool(scan_text("example.txt", "-----BEGIN PRIVATE KEY-----")),
        bool(scan_text("example.txt", "C:/Users/PrivatePerson/Documents")),
        not scan_text("test.py", 'api_key = "test-provider-key"'),
        not scan_text("example.yml", "value: ${{ secrets.GITHUB_TOKEN }}"),
        is_private("server/.env.example"), is_private("server/data.db"),
        is_private("hermes-skill/oldx-avatar/__pycache__/x.pyc"),
        is_public_image("src/assets/mayi-logo.webp"),
        not is_public_image("server/tests/private-photo.png"),
    ]
    fixture_name = "scripts/test-video-desktop.cjs"
    fixture_lines = (SOURCE_ROOT / fixture_name).read_text(encoding="utf-8-sig").splitlines()
    pinned_line = next((line for line in fixture_lines if hashlib.sha256(line.encode()).hexdigest() in TEST_FIXTURE_LINE_HASHES[fixture_name]), None)
    checks.extend((
        pinned_line is not None,
        pinned_line is not None and not scan_text(fixture_name, pinned_line),
        pinned_line is not None and bool(scan_text("electron/untrusted.cjs", pinned_line)),
        pinned_line is not None and bool(scan_text(fixture_name, pinned_line + ' api_key="' + "a" * 32 + '"')),
    ))
    if not all(checks):
        raise AssertionError("Public source preparation self-test failed")
    return {"status": "PASS", "self_test_checks": len(checks), "git_operations": 0, "network_operations": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output-parent", type=Path, default=OUTPUT_PARENT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        result = self_test()
    else:
        root = args.source_root.absolute()
        files, excluded = select_sources(root)
        findings = scan_sources(files, root)
        result = {"status": "BLOCKED" if findings else "PASS", "dry_run": args.dry_run,
            "selected_file_count": len(files), "excluded_member_count": len(excluded), "findings": findings,
            "git_operations": 0, "network_operations": 0}
        if not findings and not args.dry_run:
            result = write_snapshot(root, files, args.output_parent.absolute(), excluded)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, AssertionError) as error:
        # No source line or credential value is ever printed with a failure.
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__, "message": str(error), "git_operations": 0, "network_operations": 0}, ensure_ascii=False))
        raise SystemExit(1)
