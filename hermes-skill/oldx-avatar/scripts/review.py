"""Present, approve or explicitly skip one version-bound review stage; offline only."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _cli
import _review


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["init", "status", "present", "approve", "skip"])
    ap.add_argument("stage", nargs="?", choices=_review.STAGES)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--user-approved", help="用户在对话中真实回复的原话")
    ap.add_argument("--instance", default="plus", choices=["default", "plus", "ultra"])
    ap.add_argument("--concurrency", type=int)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--seed")
    a = ap.parse_args()
    if a.action in ("present", "approve", "skip") and not a.stage:
        ap.error("此操作需要指定一个 stage")
    if a.action == "init":
        _review.initialize(a.workdir)
    if a.action in ("init", "status"):
        _cli.report(review_enabled=_review.read(a.workdir) is not None,
                    reviews={s: _review.stage_status(a.workdir, s) for s in _review.STAGES})
    elif a.action == "present":
        context = _review.generation_context(a) if a.stage == "generation" else None
        _cli.report(**_review.present(a.workdir, a.stage, context))
    else:
        _cli.report(**_review.decide(a.workdir, a.stage, a.user_approved, a.action == "skip"))


if __name__ == "__main__":
    _cli.run(main)
