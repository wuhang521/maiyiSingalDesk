"""Synthetic POSIX byte/uid/gid/mode restoration in one new /var/tmp directory.

Run this standalone script through the approved server connection. It does not
read application .env files, databases, backups, system users or service state.
Only files created by this invocation may be changed or cleaned.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid


PREFIX = "signaldesk-kb063-rollback-"
BASE = Path("/var/tmp")
SPECS = {
    "app/knowledge_silk.py": {
        "uid": 994, "gid": 990, "mode": 0o640,
        "content": b'SYNTHETIC_FIXTURE = "metadata restoration only"\n',
    },
    ".env": {
        "uid": 994, "gid": 990, "mode": 0o600,
        "content": b"TASK_KNOWLEDGE_SILK_ENABLED=true\nTASK_SYNTHETIC_ONLY=1\n",
    },
    "hermes-plugin/__init__.py": {
        "uid": 1001, "gid": 1001, "mode": 0o644,
        "content": b'SYNTHETIC_PLUGIN = "no production import"\n',
    },
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def metadata(path):
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    assert not path.is_symlink()
    data = path.read_bytes()
    return {
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "bytes": len(data),
        "sha256": sha(data),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep this invocation's synthetic directory for inspection")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Requires a privileged POSIX controller to chown synthetic files; nothing created")
    assert BASE.is_absolute() and BASE.is_dir() and not BASE.is_symlink()
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix=PREFIX, dir=str(BASE)))
    root.chmod(0o755)
    origin = root.stat()
    assert root.parent == BASE and root.name.startswith(PREFIX)
    assert root.resolve() == root and root.is_dir() and not root.is_symlink()
    marker_token = uuid.uuid4().hex
    marker = root / ".owned-synthetic-rollback"
    marker.write_text(marker_token, encoding="ascii")
    marker.chmod(0o600)

    def owned_path(relative):
        path = root / relative
        assert not Path(relative).is_absolute() and ".." not in Path(relative).parts
        assert path.resolve().is_relative_to(root.resolve())
        for parent in [path.parent, *path.parent.parents]:
            if parent == root.parent:
                break
            assert not parent.is_symlink()
        return path

    def replace(relative, data, meta):
        """Same write→chown→chmod→os.replace order used by the publish helper."""
        target = owned_path(relative)
        assert target.is_file() and not target.is_symlink()
        temp = target.with_name("." + target.name + ".restore-" + uuid.uuid4().hex)
        assert temp.parent == target.parent and not temp.exists()
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chown(temp, meta["uid"], meta["gid"])
            temp.chmod(meta["mode"])
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()

    result = {
        "passed": False,
        "scope": "synthetic POSIX metadata restoration; not a production rollback",
        "root": str(root),
        "production_changed": False,
        "real_env_read": False,
        "database_accessed": False,
        "production_backup_read": False,
        "system_user_or_service_changed": False,
        "qq_sent": False,
        "files": [],
    }
    try:
        backup = root / "backup"
        backup.mkdir(mode=0o700)
        original = {}
        for name, spec in SPECS.items():
            path = owned_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.parent != root:
                path.parent.chmod(0o755)
            with path.open("xb") as stream:
                stream.write(spec["content"])
            os.chown(path, spec["uid"], spec["gid"])
            path.chmod(spec["mode"])
            original[name] = metadata(path)
            copy = backup / name
            copy.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            copy.write_bytes(path.read_bytes())
            copy.chmod(0o600)
        (backup / "file-metadata.json").write_text(json.dumps(original, indent=2), encoding="utf-8")

        wrong = {}
        for name in SPECS:
            path = owned_path(name)
            os.chown(path, 0, 0)
            path.chmod(0o600)
            path.write_bytes(b"SYNTHETIC_BAD_CONTENT\n")
            path.chmod(0o777)
            wrong[name] = metadata(path)
            assert wrong[name]["sha256"] != original[name]["sha256"]
            assert (wrong[name]["uid"], wrong[name]["gid"], wrong[name]["mode"]) != (
                original[name]["uid"], original[name]["gid"], original[name]["mode"])

        for name, meta in original.items():
            copy = backup / name
            assert sha(copy.read_bytes()) == meta["sha256"]
            replace(name, copy.read_bytes(), meta)
            actual = metadata(owned_path(name))
            assert actual == meta, name
            result["files"].append({
                "path": name, "before": meta, "fault": wrong[name], "restored": actual,
                "restored_mode_octal": oct(actual["mode"]),
            })

        verifier = r'''
import hashlib,json,os,sys
from pathlib import Path
assert os.geteuid()==994 and os.getegid()==990
expected=json.loads(sys.argv[1]);root=Path.cwd()
for relative,digest in expected.items():
    assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==digest
print(json.dumps({'uid':os.geteuid(),'gid':os.getegid(),'files_read_and_hashed':len(expected),'contents_exported':False}))
'''
        child = subprocess.run(
            [sys.executable, "-c", verifier, json.dumps({name: row["sha256"] for name, row in original.items()})],
            cwd=root, user=994, group=990, extra_groups=[],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
        )
        assert child.returncode == 0, "Synthetic service-user read failed"
        proof = json.loads(child.stdout)
        assert proof["uid"] == 994 and proof["files_read_and_hashed"] == len(SPECS)
        result.update(
            passed=True,
            service_identity_read=proof,
            backup_bytes_and_metadata_verified=True,
            incorrect_content_owner_and_mode_injected=True,
            atomic_replace_restored_all_metadata=True,
            only_synthetic_files=True,
        )
    except Exception as error:
        result.update(error_type=type(error).__name__, detail=str(error)[:180])
    finally:
        if args.keep:
            result["synthetic_directory_retained"] = True
            result["synthetic_directory_cleaned"] = False
            (root / "restoration-proof.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        else:
            # Refuse recursive cleanup if the freshly created root was replaced.
            current = root.lstat()
            assert stat.S_ISDIR(current.st_mode) and not root.is_symlink()
            assert (current.st_dev, current.st_ino) == (origin.st_dev, origin.st_ino)
            assert root.resolve() == root and root.parent == BASE and root.name.startswith(PREFIX)
            assert marker.read_text(encoding="ascii") == marker_token
            shutil.rmtree(root)
            assert not root.exists()
            result["synthetic_directory_retained"] = False
            result["synthetic_directory_cleaned"] = True
        print(json.dumps(result, ensure_ascii=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
