"""Local, version-bound review gates. No network access and no credential reads."""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import _cli

FILE = "review_hooks.json"
STAGES = ("inputs", "voice", "generation", "video")
LABELS = dict(zip(STAGES, ("文案与人物图", "配音试听", "生成方案与范围", "成片效果")))
MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a", ".flac"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(wd):
    path = Path(wd) / FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("stages"), dict):
            raise ValueError("unsupported review ledger")
        return data
    except (OSError, ValueError, AttributeError) as exc:
        _cli.fail(_cli.ERROR, "阶段确认记录损坏；请检查 %s：%s" % (path, exc))


def save(wd, data):
    fd, tmp = tempfile.mkstemp(prefix="review_", suffix=".tmp", dir=wd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp, Path(wd) / FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def initialize(wd):
    data = read(wd)
    if data is None:
        if not (Path(wd) / "copy.txt").is_file():
            _cli.fail(_cli.USAGE, "先建立项目并保存 copy.txt")
        data = {"version": 1, "created_at": now(), "stages": {}, "events": []}
        save(wd, data)
    return data


def generation_context(args, runtime=None):
    if runtime is None:
        from avatar import common as runtime
    root = Path(args.workdir)
    plan = json.loads((root / "avatar/plan.json").read_text(encoding="utf-8"))
    result_path = root / "avatar/result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    selected = [s for s in plan["segments"] if args.all or args.only is None or s["i"] in args.only]
    todo = [s for s in selected if args.all or args.only is not None or not
            os.path.isfile(result.get("results", {}).get(str(s["i"]), {}).get("file", ""))]
    seconds = sum(s["duration"] for s in todo)
    context = {"instance": args.instance, "concurrency": min(runtime.concurrency(args.concurrency), len(plan["segments"])),
            "only": sorted(set(args.only)) if args.only is not None else None,
            "all": bool(args.all), "seed": args.seed, "app_id": runtime.app_id(), "workflow": runtime.workflow()[0],
            "todo": [s["i"] for s in todo], "seconds": seconds}
    if os.environ.get("OLDX_HERMES") == "1" and os.environ.get("OLDX_COST_REVIEW_REQUIRED") == "0":
        return {**context, "cost_review_required": False}
    lo, mid, hi, basis = runtime.cost_estimate(seconds)
    return {**context, "estimate_coins": {"low": lo, "mid": mid, "high": hi, "basis": basis}}


def snapshot(wd, stage, context=None):
    root = Path(wd).resolve()
    paths = [root / "copy.txt"]
    assets = sorted(p for p in (root / "assets").rglob("*") if p.is_file() and p.suffix.lower() in MEDIA)
    paths += assets
    if not assets and (root / "avatar/image.png").is_file():
        paths.append(root / "avatar/image.png")
    if stage in ("voice", "generation"):
        paths += [root / p for p in ("voice.json", "emotions.json", "paragraphs.json", "out/merged.wav", "out/words.json")]
    if stage == "generation":
        paths += [root / p for p in ("avatar/image.png", "avatar/image_info.json", "avatar/plan.json", "avatar/prompt_src.json")]
        paths += sorted((root / "avatar/prompts").glob("*.txt"))
        # Bind the exact uploaded image and segment audio, including external paths.
        for name, field in (("image_info.json", "uploaded_image"), ("plan.json", "audio")):
            p = root / "avatar" / name
            if p.is_file():
                doc = json.loads(p.read_text(encoding="utf-8"))
                rows = doc.get("segments", []) if name == "plan.json" else [doc]
                for row in rows:
                    if row.get(field):
                        ref = Path(row[field])
                        paths.append(ref if ref.is_absolute() else root / ref)
    if stage == "video":
        paths += [root / p for p in ("avatar/final_avatar.mp4", "avatar/words_final.json")]
    required = {"inputs": ["copy.txt"], "voice": ["out/merged.wav"],
                "generation": ["out/merged.wav", "avatar/image_info.json", "avatar/plan.json"],
                "video": ["avatar/final_avatar.mp4"]}[stage]
    missing = [p for p in required if not (root / p).is_file()]
    if stage == "generation":
        for p in paths:
            if (p.suffix.lower() in MEDIA or p.parent.name == "prompts") and not p.is_file():
                missing.append(str(p))
    files = {}
    for p in sorted(set(p.resolve() for p in paths)):
        if p.is_file():
            digest = hashlib.sha256()
            with p.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            files[str(p)] = digest.hexdigest()
        else:
            files[str(p)] = None
    body = {"files": files, "context": context or {}}
    sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    return {"fingerprint": sha, "artifacts": list(files), "context": context or {}, "missing": missing}


def stage_status(wd, stage, context=None):
    data = read(wd)
    if data is None:
        return {"state": "disabled"}
    rec = data["stages"].get(stage, {})
    snap = snapshot(wd, stage, rec.get("context", {}) if context is None else context)
    state = "missing"
    if rec:
        state = "stale" if rec.get("fingerprint") != snap["fingerprint"] else "pending"
        if state == "pending" and rec.get("decision") in ("approved", "skipped"):
            state = "consumed" if rec.get("consumed_at") else rec["decision"]
    return {"state": state, "label": LABELS[stage], "missing": snap["missing"],
            "quote": rec.get("quote"), "context": snap["context"]}


def present(wd, stage, context=None):
    data = initialize(wd)
    snap = snapshot(wd, stage, context)
    if snap["missing"]:
        _cli.fail(_cli.ERROR, "还没有可供确认的%s：缺少 %s" % (LABELS[stage], ", ".join(snap["missing"])))
    old = data["stages"].get(stage, {})
    rec = dict(old) if old.get("fingerprint") == snap["fingerprint"] else {}
    rec.update(snap, presented_at=now())
    data["stages"][stage] = rec
    data["events"].append({"stage": stage, "action": "present", "at": now(), "fingerprint": snap["fingerprint"]})
    save(wd, data)
    return {"review_stage": stage, "label": LABELS[stage], **snap,
            "wait_for_user": stage_status(wd, stage)["state"] not in ("approved", "skipped"),
            "next": "向用户展示这一版%s并等待回复；再用 review.py approve/skip %s 记录真实原话" % (LABELS[stage], stage)}


def decide(wd, stage, quote, skip=False):
    data = initialize(wd)
    rec = data["stages"].get(stage)
    if not quote or not quote.strip():
        _cli.fail(_cli.USAGE, "必须填写用户真实回复的原话，不能由 agent 编造")
    if not rec or not rec.get("presented_at"):
        _cli.fail(_cli.NEED_USER, "先用 review.py present %s 准备并展示当前版本" % stage)
    snap = snapshot(wd, stage, rec.get("context"))
    if snap["missing"] or snap["fingerprint"] != rec.get("fingerprint"):
        _cli.fail(_cli.NEED_USER, "素材或参数已变更，先重新展示%s并取得确认" % LABELS[stage])
    rec.update(decision="skipped" if skip else "approved", quote=quote.strip(), decided_at=now())
    rec.pop("consumed_at", None)
    data["events"].append({"stage": stage, "action": rec["decision"], "quote": rec["quote"],
                           "at": now(), "fingerprint": rec["fingerprint"]})
    save(wd, data)
    return {"review_stage": stage, **stage_status(wd, stage), "wait_for_user": False}


def require(wd, stage, context=None):
    status = stage_status(wd, stage, context)
    if status["state"] == "disabled":  # Legacy direct callers retain their original guards.
        return None
    if status["state"] not in ("approved", "skipped") or status["missing"]:
        _cli.report(review_stage=stage, review=status, wait_for_user=True,
                    next="停下：python scripts/review.py present %s --workdir \"%s\"；展示后等待用户确认" % (stage, wd))
        _cli.fail(_cli.NEED_USER, "%s等待用户确认（%s），--yes 不代替阶段确认" % (LABELS[stage], status["state"]))
    return status["quote"]


def consume_generation(wd):
    data = read(wd)
    if data is not None:
        data["stages"]["generation"]["consumed_at"] = now()
        data["events"].append({"stage": "generation", "action": "consume", "at": now()})
        save(wd, data)
