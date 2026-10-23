"""Hermes-specific submission receipt checks; every HTTP call is mocked."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/avatar"))
import run_avatar as R


class HermesSubmissionGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hermes_submit_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "avatar").mkdir()
        self.env = patch.dict(os.environ, OLDX_HERMES="1", DH_WORKSPACE=str(self.root))
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_ambiguous_network_response_is_not_retried(self):
        with patch.object(R.C, "http_json", return_value=(0, {"message": "mock connection lost"})) as call:
            with self.assertRaises(R.NeedsReview):
                R.submit(1, {}, "offline-test-only")
            with self.assertRaises(R.NeedsReview):
                R.submit(1, {}, "offline-test-only")
            self.assertEqual(call.call_count, 1)
        marker = json.loads((self.root / "avatar/submission_1.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["status"], "submitting")

    def test_success_writes_recoverable_provider_receipt(self):
        with patch.object(R.C, "http_json", return_value=(200, {"taskId": "rh-offline-1", "status": "RUNNING"})) as call:
            self.assertEqual(R.submit(1, {}, "offline-test-only"), "rh-offline-1")
            self.assertEqual(call.call_count, 1)
        receipt = json.loads((self.root / "avatar/submission_1.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["task"], "rh-offline-1")
        self.assertEqual(receipt["status"], "submitted")

    def test_explicit_refusal_stops_without_automatic_queue_retry(self):
        with patch.object(R.C, "http_json", return_value=(429, {"message": "mock rate limit"})) as call:
            with self.assertRaises(R.NeedsReview):
                R.submit(1, {}, "offline-test-only")
            self.assertEqual(call.call_count, 1)
        self.assertFalse((self.root / "avatar/submission_1.json").exists())

    def test_pending_state_is_atomically_saved(self):
        target = self.root / "avatar/result.json"
        state = R.State(str(target))
        state.set("pending", 1, {"task": "rh-offline-2"})
        restored = R.State(str(target))
        self.assertEqual(restored.get("pending", 1)["task"], "rh-offline-2")
        self.assertFalse(Path(str(target) + ".tmp").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
