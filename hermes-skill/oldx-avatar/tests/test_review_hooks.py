"""Behavior and CLI integration checks for review gates. Offline; network is mocked."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _cli
import _review as R


class Reviews(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="avatar_review_")
        self.addCleanup(self.tmp.cleanup)
        self.wd = Path(self.tmp.name) / "project"
        self.wd.mkdir()
        self.write("copy.txt", "你好，这是测试。")
        self.write("assets/person.png", b"portrait-v1")
        self.write("voice.json", {"voice_id": "test-system-voice"})
        self.write("paragraphs.json", {"paragraphs": ["你好，这是测试。"]})
        self.write("out/merged.wav", b"audio-v1")
        self.write("out/words.json", {"total_seconds": 5})
        self.write("avatar/image.png", b"image-v1")
        self.write("avatar/image_info.json", {"width": 768, "height": 1376,
                                             "uploaded_image": str(self.wd / "avatar/image.png")})
        self.write("avatar/plan.json", {"total_seconds": 5, "segments": [{"i": 1, "duration": 5,
                   "start": 0, "end": 5, "audio": str(self.wd / "out/merged.wav")}]})
        self.write("avatar/prompts/segment_01.txt", "自然挥手，最后回到原姿态。")
        self.write("avatar/final_avatar.mp4", b"video-v1")
        self.env = dict(os.environ, HOME=self.tmp.name, USERPROFILE=self.tmp.name,
                        DH_WORKSPACE=str(Path(self.tmp.name) / "workspace"),
                        DASHSCOPE_API_KEY="", RUNNINGHUB_API_KEY="", RH_APP_ID="", RH_KEY_FILE="")
        R.initialize(self.wd)

    def write(self, relative, value):
        p = self.wd / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            p.write_bytes(value)
        else:
            p.write_text(json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value, encoding="utf-8")

    def cli(self, script, *args):
        p = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args), "--json"],
                           env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])

    def approve(self, stage, context=None, skip=False):
        R.present(self.wd, stage, context)
        return R.decide(self.wd, stage, "测试用户的真实回复", skip)

    def test_new_project_initializes_hooks_and_cache_hit_keeps_them(self):
        code, report = self.cli("voice/project.py", "init", "--copy", self.wd / "copy.txt")
        self.assertEqual(code, 0, report)
        ledger = Path(report["project_dir"]) / R.FILE
        self.assertTrue(ledger.is_file())
        before = ledger.read_bytes()
        code, report = self.cli("voice/project.py", "init", "--copy", self.wd / "copy.txt")
        self.assertTrue(report["cache_hit"])
        self.assertEqual(ledger.read_bytes(), before)

    def test_approval_requires_presented_version_and_real_quote(self):
        with self.assertRaises(SystemExit) as exc:
            R.decide(self.wd, "inputs", "确认")
        self.assertEqual(exc.exception.code, 5)
        R.present(self.wd, "inputs")
        with self.assertRaises(SystemExit) as exc:
            R.decide(self.wd, "inputs", " ")
        self.assertEqual(exc.exception.code, 2)
        self.approve("inputs")
        self.assertEqual(R.require(self.wd, "inputs"), "测试用户的真实回复")

    def test_skip_applies_to_one_stage_only(self):
        self.approve("voice", skip=True)
        self.assertEqual(R.stage_status(self.wd, "voice")["state"], "skipped")
        with self.assertRaises(SystemExit) as exc:
            R.require(self.wd, "generation")
        self.assertEqual(exc.exception.code, 5)

    def test_copy_change_invalidates_inputs(self):
        self.approve("inputs")
        self.write("copy.txt", "新文案")
        self.assertEqual(R.stage_status(self.wd, "inputs")["state"], "stale")

    def test_portrait_change_invalidates_inputs(self):
        self.approve("inputs")
        self.write("assets/person.png", b"portrait-v2")
        self.assertEqual(R.stage_status(self.wd, "inputs")["state"], "stale")

    def test_audio_change_invalidates_voice_even_same_size_and_mtime(self):
        self.approve("voice")
        audio = self.wd / "out/merged.wav"
        previous = audio.stat()
        self.write("out/merged.wav", b"audio-v2")
        os.utime(audio, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertEqual(R.stage_status(self.wd, "voice")["state"], "stale")

    def test_representing_changed_version_does_not_carry_approval(self):
        self.approve("inputs")
        self.write("copy.txt", "新的版本")
        self.assertTrue(R.present(self.wd, "inputs")["wait_for_user"])
        self.assertEqual(R.stage_status(self.wd, "inputs")["state"], "pending")

    def test_cannot_approve_stale_presentation(self):
        R.present(self.wd, "voice")
        self.write("out/merged.wav", b"new-audio")
        with self.assertRaises(SystemExit) as exc:
            R.decide(self.wd, "voice", "确认")
        self.assertEqual(exc.exception.code, 5)

    def test_generation_parameters_and_external_audio_are_bound(self):
        context = {"instance": "plus", "only": [1], "estimate_coins": {"high": 65}}
        self.approve("generation", context)
        with self.assertRaises(SystemExit) as exc:
            R.require(self.wd, "generation", {**context, "instance": "ultra"})
        self.assertEqual(exc.exception.code, 5)
        external = Path(self.tmp.name) / "segment.wav"
        external.write_bytes(b"old")
        plan = json.loads((self.wd / "avatar/plan.json").read_text(encoding="utf-8"))
        plan["segments"][0]["audio"] = str(external)
        self.write("avatar/plan.json", plan)
        self.approve("generation", context)
        external.write_bytes(b"new")
        self.assertEqual(R.stage_status(self.wd, "generation")["state"], "stale")

    def test_one_generation_approval_is_consumed_once(self):
        self.approve("generation", {"instance": "plus"})
        R.consume_generation(self.wd)
        self.assertEqual(R.stage_status(self.wd, "generation")["state"], "consumed")
        with self.assertRaises(SystemExit) as exc:
            R.require(self.wd, "generation", {"instance": "plus"})
        self.assertEqual(exc.exception.code, 5)

    def test_replacing_video_requires_review_again(self):
        self.approve("video")
        self.write("avatar/final_avatar.mp4", b"video-v2")
        code, report = self.cli("avatar/jumpcut.py", "--workdir", self.wd, "--yes", "--user-approved", "旧确认")
        self.assertEqual(code, 5, report)
        self.assertEqual(report["review_stage"], "video")
        self.assertFalse((self.wd / "avatar/review.json").exists())

    def test_yes_cannot_bypass_inputs_before_synthesis(self):
        code, report = self.cli("voice/synth.py", "--workdir", self.wd, "--yes")
        self.assertEqual(code, 5, report)
        self.assertEqual(report["review_stage"], "inputs")

    def test_unapproved_rerun_leaves_results_and_clips_untouched(self):
        clip = self.wd / "avatar/clips/seg_01.mp4"
        self.write("avatar/clips/seg_01.mp4", b"paid-clip")
        self.write("avatar/result.json", {"results": {"1": {"file": str(clip)}}, "pending": {"1": {"task": "paid-task"}}})
        result = (self.wd / "avatar/result.json").read_bytes()
        code, report = self.cli("avatar/run_avatar.py", "--workdir", self.wd, "--yes", "--only", "1", "--legacy")
        self.assertEqual(code, 5, report)
        self.assertEqual((self.wd / "avatar/result.json").read_bytes(), result)
        self.assertEqual(clip.read_bytes(), b"paid-clip")
        self.assertFalse((self.wd / "voice_review.json").exists())

    def test_generation_gate_cannot_be_bypassed_with_voice_flag(self):
        self.approve("inputs")
        self.approve("voice")
        code, report = self.cli("avatar/run_avatar.py", "--workdir", self.wd, "--yes", "--voice-approved", "确认声音", "--legacy")
        self.assertEqual(code, 5, report)
        self.assertEqual(report["review_stage"], "generation")

    def test_free_preview_and_status_do_not_mutate_ledger(self):
        ledger = (self.wd / R.FILE).read_bytes()
        code, report = self.cli("avatar/run_avatar.py", "--workdir", self.wd, "--legacy")
        self.assertEqual(code, 0, report)
        self.assertTrue(report["preview"])
        self.assertGreater(report["review_context"]["estimate_coins"]["high"], 0)
        code, report = self.cli("status.py", "--workdir", self.wd)
        self.assertEqual(code, 0, report)
        self.assertIn("reviews", report)
        self.assertEqual((self.wd / R.FILE).read_bytes(), ledger)

    def test_corrupt_ledger_fails_closed(self):
        self.write(R.FILE, "{broken")
        code, report = self.cli("avatar/run_avatar.py", "--workdir", self.wd, "--yes", "--legacy")
        self.assertEqual(code, 1, report)
        self.assertIn("确认记录损坏", report["error"])

    def test_pipeline_presents_voice_and_video_after_success(self):
        import pipeline
        for stage, gate in (("voice", "voice"), ("finish", "video")):
            with self.subTest(stage=stage), \
                 patch.object(sys, "argv", ["pipeline.py", stage, "--workdir", str(self.wd), "--yes"]), \
                 patch.object(pipeline, "run_steps", return_value=([], {"issues": []})):
                pipeline.main()
                self.assertEqual(R.stage_status(self.wd, gate)["state"], "pending")

    def test_pending_resume_does_not_require_a_new_generation_grant(self):
        # Exercise main with every cloud operation mocked, so this test can never spend credits.
        spec = importlib.util.spec_from_file_location("review_test_run_avatar", SCRIPTS / "avatar/run_avatar.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.write("avatar/result.json", {"pending": {"1": {"task": "existing-task", "seed": 42}}})
        with patch.object(sys, "argv", ["run_avatar.py", "--workdir", str(self.wd), "--legacy", "--yes"]), \
             patch.object(module.C, "require_key", return_value="offline-test-only"), \
             patch.object(module.C, "upload", return_value="mock-image"), \
             patch.object(module, "run_one", return_value=None) as run_one, \
             patch.object(R, "require", side_effect=AssertionError("resume must not require new approval")):
            module.main()
            self.assertEqual(run_one.call_count, 1)
            self.assertFalse(run_one.call_args.kwargs["allow_submit"])
        self.assertEqual(R.stage_status(self.wd, "generation")["state"], "missing")

    def test_approved_generation_proceeds_and_consumes_grant_with_cloud_mocked(self):
        spec = importlib.util.spec_from_file_location("review_test_approved", SCRIPTS / "avatar/run_avatar.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = SimpleNamespace(workdir=str(self.wd), instance="plus", concurrency=None, only=None, all=False, seed=None)
        self.approve("inputs")
        self.approve("voice")
        self.approve("generation", R.generation_context(args, module.C))
        with patch.object(sys, "argv", ["run_avatar.py", "--workdir", str(self.wd), "--legacy", "--yes"]), \
             patch.object(module.C, "require_key", return_value="offline-test-only"), \
             patch.object(module.C, "upload", return_value="mock-image") as upload, \
             patch.object(module, "run_one", return_value=None) as run_one:
            module.main()
            self.assertEqual(upload.call_count, 1)
            self.assertTrue(run_one.call_args.kwargs["allow_submit"])
        self.assertEqual(R.stage_status(self.wd, "generation")["state"], "consumed")
        voice = json.loads((self.wd / "voice_review.json").read_text(encoding="utf-8"))
        self.assertEqual(voice["approved"], "测试用户的真实回复")

    def test_failed_or_stale_resumed_task_cannot_submit_a_replacement(self):
        spec = importlib.util.spec_from_file_location("review_test_resume", SCRIPTS / "avatar/run_avatar.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for outcome in ("STALE", "FAILED"):
            with self.subTest(outcome=outcome):
                state = module.State(str(self.wd / "avatar/resume-test.json"))
                state.set("pending", 1, {"task": "already-paid"})
                with patch.object(module, "poll", return_value=(outcome, {"errorCode": 805})), \
                     patch.object(module, "submit", side_effect=AssertionError("must not submit")) as submit, \
                     patch.object(module.C, "upload", side_effect=AssertionError("must not upload")):
                    with self.assertRaises(module.NeedsReview):
                        module.run_one({"i": 1}, "prompt", "image", 768, 1376, "mock", "plus", str(self.wd),
                                       1, 1, state, backoff=0, allow_submit=False)
                    submit.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
