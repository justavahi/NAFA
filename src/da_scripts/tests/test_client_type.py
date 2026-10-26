"""Regression for participant dictionary values; do not guess CLOSE_CLIENT."""
import unittest
import io
from contextlib import redirect_stdout
import pandas as pd

from analytics.metrics_f import f10_client_type
from analytics.data_io import dedup_clients


class ClientTypeTest(unittest.TestCase):
    def test_dedup_handles_missing_ids_without_logging_raw_identifiers(self):
        raw = "PRIVATE-TEST-CLIENT-ID"
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = dedup_clients(pd.DataFrame({"SYNTHETIC_U": [None, raw, raw]}))
        self.assertEqual(len(result), 1)
        self.assertNotIn(raw, stream.getvalue())

    def test_participant_categories_and_unknowns(self):
        frame = pd.DataFrame({"CL_TYPE": ["CLIENT", "PROSPECT", "CLOSE_CLIENT", None, " existing "]})
        result = f10_client_type(frame)["F10_cl_type"]
        self.assertEqual(result.iloc[0], "existing")
        self.assertEqual(result.iloc[1], "potential")
        self.assertTrue(pd.isna(result.iloc[2]))
        self.assertTrue(pd.isna(result.iloc[3]))
        self.assertEqual(result.iloc[4], "existing")
