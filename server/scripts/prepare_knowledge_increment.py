"""Offline release candidate preparation only. No network, DB import, publish or restart.

Run on the deployment host with an unpacked trusted source payload. Default is
read-only plan. --output creates a new private /var/tmp candidate, not live code.
Main/config changes add only missing knowledge imports/routes/settings, preserving
unrelated task/content/user changes. All source hashes go into the manifest.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil

ROUTERS = ["knowledge_teams", "knowledge_notifications", "knowledge_media", "knowledge_capture", "knowledge_costs"]
HEAD = "20261008_12"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
def safe_file(root, relative):
    value = root / relative
    if value.is_symlink() or value.resolve().parent != value.parent.resolve() or not value.is_file(): raise ValueError("Missing or unsafe source file: " + relative)
    if not value.resolve().is_relative_to(root.resolve()): raise ValueError("Source escapes approved root")
    return value

def copy_source_tree(source, destination):
    for file in source.rglob("*"):
        if not file.is_file() or file.suffix != ".py" and file.name != "script.py.mako": continue
        relative = file.relative_to(source).as_posix(); safe_file(source, relative)
        target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)

def fields(text):
    tree = ast.parse(text)
    settings = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    return {n.target.id: n for n in settings.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}

def patch_settings(live, payload):
    old, new = fields(live), fields(payload); missing = []
    for name, node in new.items():
        if not name.startswith("knowledge_"): continue
        if name in old:
            if ast.dump(old[name], include_attributes=False) != ast.dump(node, include_attributes=False):
                raise ValueError("Existing knowledge setting differs; review explicitly: " + name)
            continue
        source = ast.get_source_segment(payload, node)
        if not source: raise ValueError("Setting source unavailable")
        missing.append("    " + source.replace("\n", "\n    "))
    if not missing: return live
    anchor = "class Settings(BaseSettings):"
    if live.count(anchor) != 1: raise ValueError("Settings anchor changed; refuse broad rewrite")
    result = live.replace(anchor, anchor + "\n" + "\n".join(missing), 1)
    if any("AliasChoices(" in source for source in missing):
        tree = ast.parse(result)
        imported = any(isinstance(node, ast.ImportFrom) and node.module == "pydantic" and
                       any(alias.name == "AliasChoices" and alias.asname in (None, "AliasChoices") for alias in node.names)
                       for node in tree.body)
        if not imported:
            lines = result.splitlines(keepends=True)
            position = max((node.end_lineno for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "__future__"), default=0)
            lines.insert(position, "from pydantic import AliasChoices\n")
            result = "".join(lines)
    return result

def patch_main(live):
    for name in ROUTERS:
        line = f"from .{name} import router as {name}_router"
        if line not in live:
            anchor = "from .knowledge_gateway import router as knowledge_gateway_router"
            if live.count(anchor) != 1: raise ValueError("Gateway import anchor changed")
            live = live.replace(anchor, anchor + "\n" + line, 1)
        line = f"app.include_router({name}_router)"
        if line not in live:
            anchor = "app.include_router(knowledge_gateway_router)"
            if live.count(anchor) != 1: raise ValueError("Gateway router anchor changed")
            live = live.replace(anchor, anchor + "\n" + line, 1)
    if "async def private_knowledge_responses(" not in live:
        live += '\n\n@app.middleware("http")\nasync def private_knowledge_responses(request, call_next):\n    response = await call_next(request)\n    if request.url.path.startswith("/api/v1/knowledge"):\n        response.headers["Cache-Control"] = "private, no-store"\n        response.headers["X-Content-Type-Options"] = "nosniff"\n    return response\n'
    return live

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, help="Trusted unpacked project directory containing server/")
    parser.add_argument("--baseline", default="/opt/signaldesk-task")
    parser.add_argument("--output", help="New /var/tmp/signaldesk-knowledge-candidate-* directory; never the live root")
    args = parser.parse_args()
    payload = Path(args.payload).resolve(); baseline = Path(args.baseline).resolve()
    if not payload.is_dir() or not baseline.is_dir() or payload == baseline: raise ValueError("Distinct payload and baseline directories required")
    source = payload / "server"
    names = ["app/" + p.name for p in sorted((source / "app").glob("knowledge*.py"))]
    names += ["migrations/versions/" + p.name for p in sorted((source / "migrations/versions").glob("20261008_*.py"))]
    if not names or not any(HEAD in name for name in names): raise ValueError("Expected knowledge migration head absent")
    original_bytes = {name: safe_file(baseline, name).read_bytes() for name in ("app/main.py", "app/config.py", "migrations/env.py")}
    original_main = original_bytes["app/main.py"].decode("utf-8-sig")
    original_config = original_bytes["app/config.py"].decode("utf-8-sig")
    main_text = patch_main(original_main)
    config_text = patch_settings(original_config, safe_file(source, "app/config.py").read_text(encoding="utf-8-sig"))
    migration_env = original_bytes["migrations/env.py"].decode("utf-8-sig")
    anchor = 'config.set_main_option("sqlalchemy.url", get_settings().database_url)'
    replacement = 'config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))'
    if anchor in migration_env:
        if migration_env.count(anchor) != 1: raise ValueError("Migration URL anchor changed")
        migration_env = migration_env.replace(anchor, replacement, 1)
    elif replacement not in migration_env: raise ValueError("Unknown migration URL handling")
    ast.parse(main_text); ast.parse(config_text); ast.parse(migration_env)
    plan = []
    for name in names:
        file = safe_file(source, name); ast.parse(file.read_text(encoding="utf-8-sig"))
        before = baseline / name
        if before.is_symlink(): raise ValueError("Live source symlink requires explicit review")
        if name.startswith("migrations/") and before.exists():
            # Existing migrations are immutable; newline-only differences are harmless.
            old = before.read_text(encoding="utf-8-sig"); new = file.read_text(encoding="utf-8-sig")
            if old != new: raise ValueError("Existing migration differs; do not overwrite: " + name)
        plan.append(dict(path=name, live_sha256=sha(before), payload_sha256=sha(file)))
    for name in ("app/main.py", "app/config.py", "migrations/env.py"):
        plan.append(dict(path=name, live_sha256=hashlib.sha256(original_bytes[name]).hexdigest(), patch="knowledge-only additive"))
    def check_baseline():
        if any(sha(baseline / entry["path"]) != entry["live_sha256"] for entry in plan):
            raise ValueError("Baseline changed while preparing; candidate is not approved")
    check_baseline()
    manifest = dict(expected_schema_before="20261008_03 (confirm on host before publish)", schema_after=HEAD,
        files=plan, not_copied=[".env", "databases", "knowledge originals", "Hermes config", "native Hermes knowledge base"],
        performed=["AST syntax parsing"], not_performed=["imports", "migrations", "tests", "paid inference", "deployment", "restart"])
    if args.output:
        proposed = Path(args.output)
        if not proposed.is_absolute() or proposed.parent.resolve() != Path("/var/tmp").resolve() or not proposed.name.startswith("signaldesk-knowledge-candidate-") or proposed.exists() or proposed.is_symlink():
            raise ValueError("Output must be a NEW narrowly named candidate under /var/tmp")
        proposed.mkdir(mode=0o700)
        for directory in ("app", "migrations"):
            copy_source_tree(baseline / directory, proposed / directory)
        for name in names:
            destination = proposed / name; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination)
        (proposed / "app/main.py").write_text(main_text, encoding="utf-8")
        (proposed / "app/config.py").write_text(config_text, encoding="utf-8")
        (proposed / "migrations/env.py").write_text(migration_env, encoding="utf-8")
        for name in ("requirements.txt", "alembic.ini"):
            shutil.copy2(safe_file(baseline, name), proposed / name)
        check_baseline()
        (proposed / "knowledge-release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        manifest["candidate"] = str(proposed)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
