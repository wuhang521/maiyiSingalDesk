"""Execute portable avatar billing and submission code with all provider I/O replaced."""
import ast
import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import re
import sys
import threading
import time
from types import SimpleNamespace

import pytest


SOURCE = (Path(__file__).resolve().parents[2]
          / "hermes-skill/oldx-avatar/scripts/avatar/run_avatar.py")


@pytest.fixture
def avatar(monkeypatch):
    """Isolate legacy flat imports while running the actual production definitions."""
    monkeypatch.setenv("OLDX_HERMES", "1")
    monkeypatch.delenv("OLDX_AVATAR_GENERATION_CONSENT", raising=False)
    monkeypatch.delenv("OLDX_COST_REVIEW_REQUIRED", raising=False)
    monkeypatch.delenv("OLDX_AVATAR_RESUME_ONLY", raising=False)
    selected = {"NeedsReview", "money_value", "safe_usage", "execution_concurrency", "cost_review_required", "generation_resume_authorized",
                "remember_generation_authorization", "AvatarBudget", "State", "run_one", "main",
                "voice_approval", "check_voice_approval"}
    definitions = [node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                   if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in selected]
    observed = {"uploads": [], "submitted": [], "polled": []}
    def upload(path, key):
        observed["uploads"].append(path)
        return "synthetic-upload"
    def submit(segment, *args, **kwargs):
        observed["submitted"].append(segment)
        return "task-%s" % segment
    def poll(segment, task, *args):
        observed["polled"].append(task)
        return "SUCCESS", {"results": [{"outputType": "mp4", "url": "https://synthetic.invalid/video"}],
                           "usage": {"consumeMoney": "0.4", "thirdPartyConsumeMoney": "0"}}
    def download(url, destination):
        Path(destination).write_bytes(b"offline video fixture")
    namespace = {"Decimal": Decimal, "InvalidOperation": InvalidOperation, "os": os,
                 "argparse": argparse, "sys": sys, "random": random, "re": re, "ThreadPoolExecutor": ThreadPoolExecutor,
                 "VOICE_REVIEW": "voice_review.json",
                 "json": json, "threading": threading, "time": time,
                 "C": SimpleNamespace(upload=upload, NODE_AUDIO="audio", NODE_IMG="image",
                                      NODE_PROMPT="prompt", NODE_WIDTH="width", NODE_HEIGHT="height", NODE_SEED="seed"),
                 "with_retry": lambda function, *args, **kwargs: function(),
                 "submit": submit, "poll": poll, "log": lambda *args: None,
                 "urllib": SimpleNamespace(request=SimpleNamespace(urlretrieve=download)),
                 "NON_RETRYABLE": re.compile("insufficient", re.I)}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return SimpleNamespace(**namespace, observed=observed, namespace=namespace)


def segment(avatar, state, budget, directory, index=1, tries=0):
    return avatar.run_one({"i": index, "audio": "offline.wav", "duration": 5}, "offline prompt",
                          "offline-image", 544, 960, "synthetic-key", "plus", str(directory),
                          45, tries, state, interval=0, backoff=0, budget=budget)


def test_hermes_serializes_but_standalone_preserves_requested_parallelism(avatar, monkeypatch):
    assert avatar.execution_concurrency(100, 5) == 1
    monkeypatch.delenv("OLDX_HERMES")
    assert avatar.execution_concurrency(100, 5) == 5
    assert avatar.AvatarBudget.from_environment(None) is None


@pytest.mark.parametrize("amount", [None, "", "nan", "Infinity", "-1", True])
def test_missing_or_invalid_money_is_unknown(avatar, amount):
    assert avatar.money_value(amount) is None


def test_actual_zero_is_known(avatar, tmp_path):
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget(state, "1", "0.6", "job-a")
    budget.record("one", 1, "SUCCESS", {"usage": {"consumeMoney": "0", "thirdPartyConsumeMoney": "0"}})
    assert budget.committed() == 0
    assert state.get("billing", "one")["usage"]["thirdPartyConsumeMoney"] == "0"
    budget.before_submit(2)


def test_additional_reported_cost_keeps_reserve_without_double_counting(avatar, tmp_path):
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget(state, "1", "0.6", "job-a")
    budget.record("one", 1, "SUCCESS", {"usage": {"consumeMoney": "0.2", "thirdPartyConsumeMoney": "0.1"}})
    assert budget.committed() == Decimal("0.6")
    with pytest.raises(avatar.NeedsReview):
        budget.before_submit(2)
    budget.record("one", 1, "SUCCESS", {"usage": {"consumeMoney": "0.8", "thirdPartyConsumeMoney": "0.1"}})
    assert budget.committed() == Decimal("0.8")


def test_budget_stops_next_submit_before_upload_and_retains_completed_clip(avatar, tmp_path):
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget(state, "0.9", "0.6", "job-a")
    result = segment(avatar, state, budget, tmp_path)
    with pytest.raises(avatar.NeedsReview, match="预算"):
        segment(avatar, state, budget, tmp_path, index=2)
    assert Path(result["file"]).exists()
    assert avatar.observed["submitted"] == [1]
    assert avatar.observed["uploads"] == ["offline.wav"]
    assert budget.committed() == Decimal("0.4")


def test_unknown_usage_uses_reserve_and_same_job_restart_keeps_ledger(avatar, tmp_path):
    path = str(tmp_path / "result.json")
    state = avatar.State(path)
    budget = avatar.AvatarBudget(state, "1", "0.6", "job-a")
    budget.record("one", 1, "SUCCESS", {"usage": {"consumeCoins": "100"}})
    resumed = avatar.AvatarBudget(avatar.State(path), "1", "0.6", "job-a")
    assert resumed.committed() == Decimal("0.6")
    with pytest.raises(avatar.NeedsReview):
        resumed.before_submit(2)
    unrelated = avatar.AvatarBudget(avatar.State(path), "1", "0.6", "job-b")
    assert unrelated.committed() == 0
    unrelated.before_submit(2)


def test_repeated_receipt_is_deduplicated_and_preserves_previously_known_money(avatar, tmp_path):
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget(state, "1", "0.6", "job-a")
    budget.record("one", 1, "SUCCESS", {"usage": {"consumeMoney": "0.4"}})
    budget.record("one", 1, "SUCCESS", {"usage": {"consumeMoney": None}})
    assert budget.committed() == Decimal("0.4")
    assert len(state.d["billing"]) == 1


def test_pending_task_is_polled_even_when_budget_is_exhausted(avatar, tmp_path):
    state = avatar.State(str(tmp_path / "result.json"))
    state.set("pending", 1, {"task": "already-submitted", "budget_job_id": "job-a", "reserve_cny": "0.6"})
    budget = avatar.AvatarBudget(state, "0.1", "0.6", "job-a")
    result = segment(avatar, state, budget, tmp_path)
    assert result["task"] == "already-submitted"
    assert avatar.observed["submitted"] == []
    assert avatar.observed["uploads"] == []
    assert avatar.observed["polled"] == ["already-submitted"]
    assert state.get("pending", 1) is None


def test_failed_task_charge_is_saved_and_blocks_unaffordable_retry(avatar, tmp_path):
    avatar.namespace["poll"] = lambda *args: ("FAILED", {"errorMessage": "offline provider failure",
        "usage": {"consumeMoney": "0.5", "thirdPartyConsumeMoney": "0.08", "secret": "must-not-save"}})
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget(state, "1", "0.6", "job-a")
    with pytest.raises(avatar.NeedsReview, match="预算"):
        segment(avatar, state, budget, tmp_path, tries=1)
    assert avatar.observed["submitted"] == [1]
    bill = state.get("billing", "task-1")
    assert bill["status"] == "FAILED"
    assert bill["usage"] == {"consumeMoney": "0.5", "thirdPartyConsumeMoney": "0.08"}
    assert "must-not-save" not in json.dumps(state.d)


def test_charge_is_persisted_before_download_failure(avatar, tmp_path):
    def unavailable(*args):
        raise OSError("offline download failed")
    avatar.namespace["urllib"].request.urlretrieve = unavailable
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget(state, "1", "0.6", "job-a")
    with pytest.raises(OSError):
        segment(avatar, state, budget, tmp_path)
    assert budget.committed() == Decimal("0.4")
    assert state.get("pending", 1)["task"] == "task-1"


def test_unknown_submission_is_never_automatically_repeated(avatar, tmp_path):
    def ambiguous(*args, **kwargs):
        raise avatar.NeedsReview("synthetic ambiguous submission")
    avatar.namespace["submit"] = ambiguous
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget(state, "1", "0.6", "job-a")
    with pytest.raises(avatar.NeedsReview, match="ambiguous"):
        segment(avatar, state, budget, tmp_path, tries=3)
    assert len(avatar.observed["uploads"]) == 1
    assert state.d["billing"] == {}


def test_missing_budget_blocks_new_tasks_but_can_resume_pending(avatar, tmp_path, monkeypatch):
    monkeypatch.delenv("OLDX_AVATAR_BUDGET_CNY", raising=False)
    monkeypatch.delenv("OLDX_AVATAR_SEGMENT_RESERVE_CNY", raising=False)
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget.from_environment(state)
    with pytest.raises(avatar.NeedsReview, match="费用尚未确认"):
        segment(avatar, state, budget, tmp_path)
    state.set("pending", 1, {"task": "already-submitted"})
    segment(avatar, state, budget, tmp_path)
    assert avatar.observed["submitted"] == []


def test_standalone_run_keeps_unmodified_provider_usage_without_budget(avatar, tmp_path, monkeypatch):
    monkeypatch.delenv("OLDX_HERMES")
    state = avatar.State(str(tmp_path / "result.json"))
    result = segment(avatar, state, None, tmp_path)
    assert result["usage"] == {"consumeMoney": "0.4", "thirdPartyConsumeMoney": "0"}
    assert state.d["billing"] == {}


def test_immediately_failed_submit_retains_reported_charge(avatar, tmp_path, monkeypatch):
    monkeypatch.setenv("DH_WORKSPACE", str(tmp_path))
    (tmp_path / "avatar").mkdir()
    avatar.namespace["C"].BASE = "https://synthetic.invalid"
    avatar.namespace["C"].app_id = lambda: "offline-app"
    avatar.namespace["C"].http_json = lambda *args: (200, {"taskId": "failed-on-submit", "status": "FAILED",
        "usage": {"consumeMoney": "0.25", "thirdPartyConsumeMoney": "0.02"}})
    function = next(node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                    if isinstance(node, ast.FunctionDef) and node.name == "submit")
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), avatar.namespace)
    state = avatar.State(str(tmp_path / "result.json"))
    budget = avatar.AvatarBudget(state, "1", "0.6", "job-a")
    with pytest.raises(avatar.NeedsReview):
        segment(avatar, state, budget, tmp_path)
    assert state.get("billing", "failed-on-submit")["status"] == "FAILED"
    assert budget.committed() == Decimal("0.6")
    assert not (tmp_path / "avatar/submission_1.json").exists()


def main_project(avatar, tmp_path, monkeypatch, direct=False, first_run=True):
    """Real review snapshots + production main, with provider and media I/O stubbed."""
    def fail(code, message):
        raise RuntimeError(message)
    cli = SimpleNamespace(report=lambda **kwargs: None, fail=fail, ERROR=1, USAGE=2,
                          NEED_USER=5, KEY_REJECTED=4, REMOTE_FAILED=8)
    monkeypatch.setitem(sys.modules, "_cli", cli)
    spec = importlib.util.spec_from_file_location("avatar_billing_review", SOURCE.parent.parent / "_review.py")
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    avatar.namespace.update(_review=review, _cli=cli,
        _util=SimpleNamespace(file_sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()),
        TK=SimpleNamespace(prompt_sha=lambda value: hashlib.sha256(value.encode()).hexdigest(), archive=lambda *args: None))
    common = avatar.namespace["C"]
    common.concurrency = lambda requested: requested or 3
    common.verify_plan_audio = lambda *args: None
    common.workflow = lambda: ("offline-workflow", "")
    common.app_id = lambda: "offline-app"
    common.cost_estimate = lambda seconds: (1, 2, 3, "offline estimate")
    common.load_config = lambda: {"concurrency": 3}
    common.require_key = lambda: "synthetic-key"
    common.DEFAULT_SEED = 42
    common.DEFAULT_INSTANCE = "plus"
    if direct:
        monkeypatch.setenv("OLDX_COST_REVIEW_REQUIRED", "0")
        monkeypatch.setenv("OLDX_AVATAR_GENERATION_CONSENT", "1")
        common.cost_estimate = lambda *args: pytest.fail("Direct generation must not compute or query any price")
    for folder in ("avatar/prompts", "out", "assets"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "copy.txt").write_text("offline narration", encoding="utf-8")
    for name in ("voice.json", "emotions.json", "paragraphs.json", "out/words.json", "avatar/prompt_src.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    for name in ("out/merged.wav", "assets/image.png", "avatar/image.png"):
        (tmp_path / name).write_bytes(b"offline media")
    segments = []
    for index in (1, 2):
        audio = tmp_path / ("out/segment-%s.wav" % index)
        audio.write_bytes(b"offline audio")
        segments.append({"i": index, "audio": str(audio), "duration": 5})
        (tmp_path / ("avatar/prompts/segment_%02d.txt" % index)).write_text("offline motion", encoding="utf-8")
    (tmp_path / "avatar/plan.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")
    (tmp_path / "avatar/image_info.json").write_text(json.dumps({"width": 544, "height": 960,
        "uploaded_image": str(tmp_path / "assets/image.png")}), encoding="utf-8")
    args = SimpleNamespace(workdir=str(tmp_path), instance="plus", concurrency=None,
                           only=None, all=False, seed=None)
    for stage in ("inputs", "voice", "generation"):
        context = review.generation_context(args, common) if stage == "generation" else None
        review.present(str(tmp_path), stage, context)
        review.decide(str(tmp_path), stage, "Synthetic user approval for offline test")
    monkeypatch.setenv("OLDX_AVATAR_BUDGET_CNY", "" if direct else "2")
    monkeypatch.setenv("OLDX_AVATAR_SEGMENT_RESERVE_CNY", "" if direct else "1")
    monkeypatch.setenv("OLDX_AVATAR_BUDGET_JOB_ID", "job-main")
    monkeypatch.setattr(sys, "argv", ["run_avatar.py", "--workdir", str(tmp_path), "--yes",
                                    "--retries", "0", "--timeout-min", "1"])
    success = avatar.namespace["poll"]
    if not first_run:
        return review, args, avatar.State(str(tmp_path / "avatar/result.json"))
    avatar.namespace["poll"] = lambda *args: ("TIMEOUT", None)
    with pytest.raises(SystemExit):
        avatar.main()
    assert avatar.observed["submitted"] == [1]
    state = avatar.State(str(tmp_path / "avatar/result.json"))
    assert state.get("pending", 1)["task"] == "task-1"
    assert state.get("pending", 2) is None
    assert review.stage_status(str(tmp_path), "generation")["state"] == "consumed"
    avatar.namespace["poll"] = success
    return review, args, state


def test_actual_main_resumes_pending_then_submits_remaining_approved_segment(avatar, tmp_path, monkeypatch):
    review, args, state = main_project(avatar, tmp_path, monkeypatch)
    avatar.main()
    final = avatar.State(str(tmp_path / "avatar/result.json"))
    assert avatar.observed["submitted"] == [1, 2]
    assert avatar.observed["polled"] == ["task-1", "task-2"]
    assert set(final.d["results"]) == {"1", "2"}
    assert final.d["pending"] == {}
    assert len(final.d["billing"]) == 2
    assert sum(row.get("action") == "consume" for row in review.read(str(tmp_path))["events"]) == 1


@pytest.mark.parametrize("changed", ["copy.txt", "assets/image.png", "avatar/plan.json", "avatar/prompts/segment_02.txt"])
def test_actual_main_never_reuses_batch_approval_after_input_change(avatar, tmp_path, monkeypatch, changed):
    main_project(avatar, tmp_path, monkeypatch)
    file = tmp_path / changed
    if changed == "avatar/plan.json":
        value = json.loads(file.read_text(encoding="utf-8"))
        value["changed"] = True
        file.write_text(json.dumps(value), encoding="utf-8")
    else:
        file.write_text("different approved material", encoding="utf-8")
    with pytest.raises(RuntimeError, match="确认"):
        avatar.main()
    assert avatar.observed["submitted"] == [1]
    assert avatar.observed["polled"] == []


def test_actual_main_never_reuses_another_job_approval(avatar, tmp_path, monkeypatch):
    main_project(avatar, tmp_path, monkeypatch)
    monkeypatch.setenv("OLDX_AVATAR_BUDGET_JOB_ID", "job-other")
    with pytest.raises(RuntimeError, match="确认"):
        avatar.main()
    assert avatar.observed["submitted"] == [1]


def test_terminal_failed_segment_needs_new_approval_before_replacement(avatar, tmp_path, monkeypatch):
    review, args, state = main_project(avatar, tmp_path, monkeypatch)
    budget = avatar.AvatarBudget.from_environment(state)
    state.drop("pending", 1)
    budget.record("task-1", 1, "FAILED", {"usage": {"consumeMoney": "0.2"}})
    context = review.generation_context(args, avatar.namespace["C"])
    assert avatar.generation_resume_authorized(args, state, context, budget) is False


def test_replay_only_and_budget_mutation_cannot_reuse_batch_authorization(avatar, tmp_path, monkeypatch):
    review, args, state = main_project(avatar, tmp_path, monkeypatch)
    budget = avatar.AvatarBudget.from_environment(state)
    context = review.generation_context(args, avatar.namespace["C"])
    assert avatar.generation_resume_authorized(args, state, context, budget) is True
    args.only = [1]
    assert avatar.generation_resume_authorized(args, state, context, budget) is False
    args.only = None
    args.all = True
    assert avatar.generation_resume_authorized(args, state, context, budget) is False
    args.all = False
    budget.maximum = Decimal("20")
    assert avatar.generation_resume_authorized(args, state, context, budget) is False


def test_direct_actual_main_first_run_and_resume_without_any_pricing(avatar, tmp_path, monkeypatch, capsys):
    review, args, state = main_project(avatar, tmp_path, monkeypatch, direct=True)
    initial = review.read(str(tmp_path))["stages"]["generation"]["context"]
    assert initial["cost_review_required"] is False and "estimate_coins" not in initial
    avatar.main()
    saved = avatar.State(str(tmp_path / "avatar/result.json"))
    assert avatar.observed["submitted"] == [1, 2]
    assert set(saved.d["results"]) == {"1", "2"}
    assert saved.get("generation_authorizations", "job-main")["generation_consent"] is True
    assert saved.get("generation_authorizations", "job-main")["maximum_cny"] == "None"
    output = capsys.readouterr().out
    assert "预计费用" not in output and "RH 币" not in output and "已知费用合计" not in output
    assert len(saved.d["billing"]) == 2  # Private receipts remain available.


def test_direct_without_generation_click_never_submits_or_prices(avatar, tmp_path, monkeypatch, capsys):
    main_project(avatar, tmp_path, monkeypatch, direct=True, first_run=False)
    monkeypatch.delenv("OLDX_AVATAR_GENERATION_CONSENT")
    with pytest.raises(SystemExit):
        avatar.main()
    assert avatar.observed["submitted"] == []
    assert "预计费用" not in capsys.readouterr().out


@pytest.mark.parametrize("job_id", [None, "", "legacy", "../job", "job id"])
def test_direct_needs_valid_job_scope_even_with_consent(avatar, tmp_path, job_id):
    state = avatar.State(str(tmp_path / "result.json"))
    direct = avatar.AvatarBudget(state, None, None, job_id, generation_consent=True)
    with pytest.raises(avatar.NeedsReview, match="有效"):
        segment(avatar, state, direct, tmp_path)
    assert avatar.observed["submitted"] == []


def test_direct_failed_task_never_retries_without_a_new_job(avatar, tmp_path):
    avatar.namespace["poll"] = lambda *args: ("FAILED", {"errorMessage": "offline failure",
        "usage": {"consumeMoney": "0.4"}})
    state = avatar.State(str(tmp_path / "result.json"))
    direct = avatar.AvatarBudget(state, None, None, "job-a", generation_consent=True)
    with pytest.raises(avatar.NeedsReview, match="新任务"):
        segment(avatar, state, direct, tmp_path, tries=5)
    with pytest.raises(avatar.NeedsReview, match="新任务"):
        segment(avatar, state, direct, tmp_path)
    assert avatar.observed["submitted"] == [1]
    assert state.get("billing", "task-1")["usage"]["consumeMoney"] == "0.4"


def test_direct_mode_cannot_reuse_legacy_budget_batch_confirmation(avatar, tmp_path, monkeypatch):
    review, args, state = main_project(avatar, tmp_path, monkeypatch)
    direct = avatar.AvatarBudget(state, "2", "1", "job-main", generation_consent=True)
    context = review.generation_context(args, avatar.namespace["C"])
    assert avatar.generation_resume_authorized(args, state, context, direct) is False


@pytest.mark.parametrize("changed", ["copy.txt", "assets/image.png", "avatar/prompts/segment_02.txt"])
def test_direct_confirmation_does_not_survive_material_changes(avatar, tmp_path, monkeypatch, changed):
    main_project(avatar, tmp_path, monkeypatch, direct=True)
    (tmp_path / changed).write_text("changed material", encoding="utf-8")
    with pytest.raises(RuntimeError, match="确认"):
        avatar.main()
    assert avatar.observed["submitted"] == [1]


def test_direct_confirmation_does_not_survive_job_change(avatar, tmp_path, monkeypatch):
    main_project(avatar, tmp_path, monkeypatch, direct=True)
    monkeypatch.setenv("OLDX_AVATAR_BUDGET_JOB_ID", "job-other")
    with pytest.raises(RuntimeError, match="确认"):
        avatar.main()
    assert avatar.observed["submitted"] == [1]


@pytest.mark.parametrize("known_failure", [True, False])
def test_new_direct_job_can_replace_only_a_definitely_failed_receipt(avatar, tmp_path, monkeypatch, known_failure):
    monkeypatch.setenv("DH_WORKSPACE", str(tmp_path))
    (tmp_path / "avatar").mkdir()
    marker = tmp_path / "avatar/submission_1.json"
    marker.write_text(json.dumps({"task": "old-task", "status": "submitted"}), encoding="utf-8")
    state = avatar.State(str(tmp_path / "result.json"))
    if known_failure:
        previous = avatar.AvatarBudget(state, None, None, "job-old", generation_consent=True)
        previous.record("old-task", 1, "FAILED", {"usage": {"consumeMoney": "0.2"}})
    calls = []
    avatar.namespace["C"].BASE = "https://synthetic.invalid"
    avatar.namespace["C"].app_id = lambda: "offline-app"
    def post(*args):
        calls.append(args)
        return 200, {"taskId": "new-task", "status": "RUNNING"}
    avatar.namespace["C"].http_json = post
    function = next(node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                    if isinstance(node, ast.FunctionDef) and node.name == "submit")
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), avatar.namespace)
    direct = avatar.AvatarBudget(state, None, None, "job-new", generation_consent=True)
    if known_failure:
        result = segment(avatar, state, direct, tmp_path)
        assert result["task"] == "new-task" and len(calls) == 1
    else:
        with pytest.raises(avatar.NeedsReview, match="不明确"):
            segment(avatar, state, direct, tmp_path)
        assert calls == [] and json.loads(marker.read_text(encoding="utf-8"))["task"] == "old-task"


def migrated_resume_environment(avatar, monkeypatch):
    monkeypatch.setenv("OLDX_COST_REVIEW_REQUIRED", "0")
    monkeypatch.setenv("OLDX_AVATAR_GENERATION_CONSENT", "0")
    monkeypatch.setenv("OLDX_AVATAR_RESUME_ONLY", "1")
    monkeypatch.setenv("OLDX_AVATAR_BUDGET_CNY", "")
    monkeypatch.setenv("OLDX_AVATAR_SEGMENT_RESERVE_CNY", "")
    avatar.namespace["C"].cost_estimate = lambda *args: pytest.fail("Migrated recovery must not fetch or compute prices")
    avatar.observed["uploads"].clear()


def test_actual_main_migrated_job_only_downloads_existing_receipt(avatar, tmp_path, monkeypatch):
    review, args, _ = main_project(avatar, tmp_path, monkeypatch)
    migrated_resume_environment(avatar, monkeypatch)
    avatar.main()
    saved = avatar.State(str(tmp_path / "avatar/result.json"))
    assert avatar.observed["polled"] == ["task-1"]
    assert avatar.observed["submitted"] == [1]
    assert avatar.observed["uploads"] == []
    assert set(saved.d["results"]) == {"1"} and saved.d["pending"] == {}
    # Migration did not rewrite the old confirmation as direct consent.
    assert saved.get("generation_authorizations", "job-main")["generation_consent"] is False
    assert "estimate_coins" in review.read(str(tmp_path))["stages"]["generation"]["context"]


def test_actual_main_migrated_unknown_pending_stays_on_original_receipt(avatar, tmp_path, monkeypatch):
    main_project(avatar, tmp_path, monkeypatch)
    migrated_resume_environment(avatar, monkeypatch)
    avatar.namespace["poll"] = lambda *args: ("TIMEOUT", None)
    with pytest.raises(SystemExit):
        avatar.main()
    saved = avatar.State(str(tmp_path / "avatar/result.json"))
    assert saved.get("pending", 1)["task"] == "task-1"
    assert avatar.observed["submitted"] == [1]
    assert avatar.observed["uploads"] == []


def test_actual_main_migrated_failed_receipt_cannot_retry(avatar, tmp_path, monkeypatch):
    main_project(avatar, tmp_path, monkeypatch)
    migrated_resume_environment(avatar, monkeypatch)
    avatar.namespace["poll"] = lambda *args: ("FAILED", {"usage": {"consumeMoney": "0.2"}})
    with pytest.raises(SystemExit):
        avatar.main()
    saved = avatar.State(str(tmp_path / "avatar/result.json"))
    assert saved.d["pending"] == {} and saved.d["results"] == {}
    assert saved.get("billing", "task-1")["status"] == "FAILED"
    assert avatar.observed["submitted"] == [1] and avatar.observed["uploads"] == []


def test_runtime_migration_finishes_old_receipt_then_new_click_generates_missing_only(avatar, tmp_path, monkeypatch):
    """Run production runtime + production CLI main across the migration boundary."""
    sys.path.insert(0, str(SOURCE.parents[4] / "server"))
    from app import video_runtime as runtime
    def complete_offline(coroutine):
        # All awaits below finish synchronously in our provider/CLI substitutes.
        # No socket-backed event loop is needed for this strictly offline chain.
        try:
            coroutine.send(None)
        except StopIteration as finished:
            return finished.value
        finally:
            coroutine.close()
        pytest.fail("Unexpected asynchronous I/O escaped the offline test seams")
    project = {"id": "offline-project", "revision": 1}
    wd = runtime._skill_workdir(tmp_path, project)
    wd.mkdir(parents=True)
    review, review_args, _ = main_project(avatar, wd, monkeypatch)
    monkeypatch.setenv("TASK_VIDEO_COST_REVIEW_REQUIRED", "0")
    avatar.namespace["C"].cost_estimate = lambda *args: pytest.fail("No migration or direct price calls")
    monkeypatch.setattr(runtime, "estimate_job", lambda *args: pytest.fail("No migration quote calls"))
    monkeypatch.setattr(runtime, "_avatar_billing_result", lambda *args: {"source": "offline-safe-ledger"})
    mapped = []
    async def map_review(_project, _wd, stage):
        mapped.append(stage)
        context = review.generation_context(review_args, avatar.namespace["C"]) if stage == "generation" else None
        review.present(str(_wd), stage, context)
        review.decide(str(_wd), stage, "New explicit offline generation click")
    monkeypatch.setattr(runtime, "_map_review", map_review)
    async def cli(_wd, script, *args, paid_environment=None, **kwargs):
        if script == "avatar/run_avatar.py":
            for name, value in paid_environment.items():
                monkeypatch.setenv(name, value)
            monkeypatch.setenv("OLDX_COST_REVIEW_REQUIRED", "0")
            try:
                avatar.main()
            except SystemExit:
                raise RuntimeError("Offline CLI retained or terminated the original receipt")
        elif script == "pipeline.py" and args[0] == "finish":
            (_wd / "avatar/final_avatar.mp4").write_bytes(b"offline finished video")
        else:
            pytest.fail("Migration must not rebuild approved media")
    monkeypatch.setattr(runtime, "_cli", cli)
    old = {"id": "job-main", "status": "running", "provider_task_id": "old-receipt",
           "budget_grant": {"approved": True, "max_cost": 2}}
    migrated = complete_offline(runtime._avatar_job(old, project, tmp_path, None))
    assert migrated["status"] == "failed"
    assert migrated["result"]["remaining_segments"] == [2]
    assert migrated["result"]["needs_new_generation_consent"] is True
    assert avatar.observed["submitted"] == [1] and mapped == []
    fresh = {"id": "job-new-direct", "status": "queued",
             "budget_grant": {"approved": True, "generation_consent": True}}
    outcome = complete_offline(runtime._avatar_job(fresh, project, tmp_path, None))
    assert outcome["status"] == "succeeded"
    assert mapped == ["inputs", "voice", "generation"]
    assert avatar.observed["submitted"] == [1, 2]
    assert avatar.observed["polled"] == ["task-1", "task-2"]
    saved = avatar.State(str(wd / "avatar/result.json"))
    assert set(saved.d["results"]) == {"1", "2"}
    assert saved.get("generation_authorizations", "job-new-direct")["generation_consent"] is True
