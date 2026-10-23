"""Read existing Hermes primary model over SSH; save current-user DPAPI ciphertext.

No provider requests, remote writes, raw secrets in argv, stdout or diagnostics.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from app.video_hermes_config import HermesConfigError, save_hermes_video_config


def remote_probe(config_path: str) -> str:
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", config_path) or ".." in Path(config_path).parts:
        raise HermesConfigError("远端配置路径无效")
    return '''
from pathlib import Path
import json
try:
    import yaml
    path=Path(CONFIG_PATH)
    cfg=yaml.safe_load(path.read_text(encoding='utf-8'))
    model=cfg.get('model') or {}
    if not isinstance(model,dict): raise ValueError()
    result={'schema':1,'provider':model.get('provider') or 'custom','model':model.get('default') or model.get('model'),
            'base_url':model.get('base_url'),'api_key':model.get('api_key')}
    if not all(isinstance(result[k],str) and result[k].strip() for k in ('provider','model','base_url','api_key')): raise ValueError()
    print(json.dumps(result,ensure_ascii=False))
except Exception:
    print(json.dumps({'error':'Existing Hermes primary model configuration unavailable'}))
    raise SystemExit(2)
'''.replace("CONFIG_PATH", repr(config_path))


def read_remote_config(args, *, probe: str | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", args.host) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", args.user):
        raise HermesConfigError("SSH 主机或用户名格式无效")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", args.container):
        raise HermesConfigError("Hermes 容器名无效")
    identity = Path(args.identity).expanduser().resolve()
    known_hosts = Path(args.known_hosts).expanduser().resolve()
    if not identity.is_file() or not known_hosts.is_file():
        raise HermesConfigError("缺少已有 SSH 身份文件或已核验主机记录")
    command = [args.ssh, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
               "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={known_hosts}", "-i", str(identity),
               f"{args.user}@{args.host}", f"docker exec -i {args.container} /opt/hermes/.venv/bin/python -B -"]
    try:
        completed = subprocess.run(command, input=probe if probe is not None else remote_probe(args.remote_config), capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HermesConfigError("只读 SSH 连接失败或超时；未导入模型配置") from exc
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > 65536:
        raise HermesConfigError("无法读取当前 Hermes 主模型；请核对已有 SSH 授权与配置路径")
    try:
        value = json.loads(completed.stdout)
    except ValueError as exc:
        raise HermesConfigError("Hermes 没有返回有效主模型配置") from exc
    if not isinstance(value, dict) or "error" in value:
        raise HermesConfigError("Hermes 当前主模型配置不完整")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--identity", required=True)
    parser.add_argument("--known-hosts", default=str(Path.home() / ".ssh/known_hosts"))
    parser.add_argument("--container", default="hermes")
    parser.add_argument("--remote-config", default="/opt/data/config.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ssh", default="ssh")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = save_hermes_video_config(read_remote_config(args), args.output, replace=args.replace)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except HermesConfigError as exc:
        # No raw remote stderr, command output, provider body or traceback.
        print(json.dumps({"configured": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
