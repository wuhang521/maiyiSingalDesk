"""Read the already-configured Hermes Ark key; no model calls or remote writes."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from app.video_hermes_scene_config import ARK_BASE, HermesSceneConfigError, save_hermes_scene_config

spec = importlib.util.spec_from_file_location("_hermes_scene_shared", Path(__file__).with_name("import-hermes-video-config.py"))
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


def remote_probe(home, model):
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", home) or ".." in Path(home).parts:
        raise HermesSceneConfigError("Hermes 配置目录无效")
    if not re.fullmatch(r"(?:doubao-seedance-|ep-)[A-Za-z0-9_.-]+", model):
        raise HermesSceneConfigError("Seedance 模型名称无效")
    return '''
from pathlib import Path
import json,os
try:
    home=Path(HOME_PATH)
    os.environ['HERMES_HOME']=str(home)
    from agent.secret_scope import load_env_file
    profile=load_env_file(home/'.env')
    key=profile.get('ARK_API_KEY')
    if not isinstance(key,str) or not key.strip(): raise ValueError()
    print(json.dumps({'schema':1,'provider':'ark','model':MODEL_NAME,'base_url':ARK_BASE,'api_key':key}))
except Exception:
    print(json.dumps({'error':'Hermes Ark credential unavailable'}))
    raise SystemExit(2)
'''.replace("HOME_PATH", repr(home)).replace("MODEL_NAME", repr(model)).replace("ARK_BASE", repr(ARK_BASE))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--identity", required=True)
    parser.add_argument("--known-hosts", default=str(Path.home() / ".ssh/known_hosts"))
    parser.add_argument("--container", default="hermes")
    parser.add_argument("--remote-home", default="/opt/data")
    parser.add_argument("--model", default="doubao-seedance-2-0-mini-260615")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ssh", default="ssh")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = shared.read_remote_config(args, probe=remote_probe(args.remote_home, args.model))
        print(json.dumps(save_hermes_scene_config(value, args.output, replace=args.replace), ensure_ascii=False))
        return 0
    except HermesSceneConfigError as error:
        print(json.dumps({"configured": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
