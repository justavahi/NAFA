"""Guard the development/holdout boundary before any model or weight tuning."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd

from analytics.golden_set import save_golden_and_holdout, split_holdout
import importlib


class HoldoutTest(unittest.TestCase):
    def test_saved_calibration_file_excludes_every_heldout_event(self):
        data = pd.DataFrame([dict(client_id=c, event_id=e, relevance=1) for c in ["A", "B"] for e in ["E1", "E2", "E3", "E4"]])
        with tempfile.TemporaryDirectory() as folder:
            result = save_golden_and_holdout(data, folder)
            dev = pd.read_csv(result["golden"])
            hold = pd.read_csv(result["holdout"])
            master = pd.read_csv(result["all_annotations"])
            self.assertTrue(set(dev.event_id).isdisjoint(hold.event_id))
            self.assertEqual(len(dev)+len(hold), len(master))
            self.assertEqual(len(master), len(data))

    def test_single_event_cannot_be_claimed_as_independent_holdout(self):
        with self.assertRaisesRegex(ValueError, "two independent"):
            split_holdout(pd.DataFrame({"event_id": ["E1", "E1"]}))

    def test_calibration_stages_never_receive_unlabelled_components(self):
        module = importlib.import_module("analytics.calibrate")
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            pd.DataFrame({"client_id": ["C", "C"], "event_id": ["DEV", "HELD_OUT"]}).to_csv(folder / "components.csv", index=False)
            pd.DataFrame({"client_id": ["C"], "event_id": ["DEV"], "relevance": [1]}).to_csv(folder / "labels.csv", index=False)
            with patch.object(module, "calibrate", return_value={"weights": [.4,.25,.1,.1,.15], "raw_ndcg": .5}) as fit, \
                 patch.object(module, "leave_one_archetype_out", return_value={"status": "fixture"}) as loao, \
                 patch.object(module, "bootstrap_ci", return_value={"mean": .5, "ci95": [.4,.6]}) as boot:
                module.run_calibration(folder / "components.csv", folder / "labels.csv", folder)
                for stage in (fit, loao, boot):
                    self.assertEqual(stage.call_args.args[0].event_id.tolist(), ["DEV"])
