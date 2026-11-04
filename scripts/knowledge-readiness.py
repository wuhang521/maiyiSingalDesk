"""Print configuration presence and local tool readiness; never print secrets."""
import json
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


def main():
    from app.config import Settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / "server" / ".env")
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file)
    names = [name for name in type(settings).model_fields if name.startswith("knowledge_") or name.startswith("prompt_")]
    values = {}
    for name in names:
        value = getattr(settings, name)
        if isinstance(value, bool):
            values[name] = value
        elif any(word in name for word in ("key", "secret", "token", "url", "command")):
            values[name + "_configured"] = bool(str(value).strip())
        elif name.endswith("_model"):
            values[name + "_configured"] = bool(str(value).strip())
    report = dict(python=sys.version.split()[0], configuration=values,
                  tools={name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe")},
                  note="Presence is not authorization, connectivity, model quality, or production health.")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
