"""Read Hermes' existing DashScope credential; save an isolated DPAPI snapshot.

The remote command only reads the current profile/process environment. It never
loads a model, calls a provider, modifies configuration, or exports the LLM key.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from app.video_hermes_voice_config import HermesVoiceConfigError, save_hermes_voice_config

_shared_spec = importlib.util.spec_from_file_location("_hermes_primary_import_shared", Path(__file__).with_name("import-hermes-video-config.py"))
_shared = importlib.util.module_from_spec(_shared_spec)
_shared_spec.loader.exec_module(_shared)


def remote_probe(home_path: str) -> str:
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", home_path) or ".." in Path(home_path).parts:
        raise HermesVoiceConfigError("Hermes 配置目录无效")
    return '''
from pathlib import Path
import json,os
try:
    home=Path(HOME_PATH)
    os.environ['HERMES_HOME']=str(home)
    # Hermes' official scope parser only reads; its full dotenv loader also
    # sanitizes files and resolves external stores, so do not call it here.
    from agent.secret_scope import load_env_file
    profile=load_env_file(home/'.env')
    key=profile.get('DASHSCOPE_API_KEY')
    source='hermes-profile-environment'
    if not key:
        found=set()
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit() or int(proc.name)==os.getpid(): continue
            try:
                pairs=dict(p.split(b'=',1) for p in (proc/'environ').read_bytes().split(b'\\0') if b'=' in p)
                if pairs.get(b'HERMES_HOME')==str(home).encode() and pairs.get(b'DASHSCOPE_API_KEY'):
                    found.add(pairs[b'DASHSCOPE_API_KEY'].decode('utf-8'))
            except (OSError,UnicodeError): pass
        if len(found)!=1: raise ValueError()
        key=found.pop()
        source='hermes-process-environment'
    if not isinstance(key,str) or not key.strip(): raise ValueError()
    print(json.dumps({'schema':1,'provider':'bailian','DASHSCOPE_API_KEY':key,'source':source}))
except Exception:
    print(json.dumps({'error':'Hermes DashScope environment credential unavailable'}))
    raise SystemExit(2)
'''.replace("HOME_PATH", repr(home_path))


def read_remote_voice_config(args) -> dict:
    try:
        return _shared.read_remote_config(args, probe=remote_probe(args.remote_home))
    except HermesVoiceConfigError as exc:
        raise HermesVoiceConfigError("无法只读取得 Hermes 百炼凭证；请核对现有 SSH 授权与配音环境") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--identity", required=True)
    parser.add_argument("--known-hosts", default=str(Path.home() / ".ssh/known_hosts"))
    parser.add_argument("--container", default="hermes")
    parser.add_argument("--remote-home", default="/opt/data")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ssh", default="ssh")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = save_hermes_voice_config(read_remote_voice_config(args), args.output, replace=args.replace)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except HermesVoiceConfigError as exc:
        print(json.dumps({"configured": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
