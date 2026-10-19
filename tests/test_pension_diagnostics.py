"""Pension detail diagnostics expose schema only; all network access is blocked."""

import json
import unittest
from unittest.mock import MagicMock, patch

import auth
import common
import win720


class PensionDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        singleton = patch.object(auth.HttpClientSingleton, "get_instance", return_value=self.client)
        singleton.start()
        self.addCleanup(singleton.stop)
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        self.auth_ctrl = auth.AuthController()
        self.game = win720.Win720()
        self.ledger = {"data": {"list": [{
            "ltEpsdView": "337회", "ltWnAmt": 0,
            "ntslOrdrNo": "private-order-identifier",
        }]}}

    def query(self, detail):
        with patch("win720.common.get_json_response", side_effect=[self.ledger, detail]):
            return self.game.check_winning(self.auth_ctrl)

    def tearDown(self):
        self.client.get.assert_not_called()
        self.client.post.assert_not_called()

    def test_original_request_error_code_is_preserved(self):
        for code in ("timeout", "unauthorized", "forbidden", "invalid_json", "api_error"):
            with self.subTest(code=code):
                with self.assertLogs("win720", level="ERROR") as logs:
                    result = self.query(common.LotteryQueryError(code))
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["round"], "337")
                self.assertEqual(result["money"], "0 원")
                self.assertEqual(result["error_code"], "detail_error")
                self.assertEqual(result["detail_error_code"], code)
                self.assertEqual(result["detail_failure_stage"], "request")
                self.assertEqual(result["detail_failure_reason"], "request_failed")
                self.assertIn("code=" + code, "\n".join(logs.output))
                self.assertNotIn("private-order-identifier", "\n".join(logs.output))

    def test_existing_parser_rejections_have_distinct_fixed_reasons(self):
        cases = (
            ({"data": None}, "response_schema", "invalid_data"),
            ({"data": {}}, "response_schema", "invalid_list"),
            ({"data": {"list": []}}, "response_schema", "empty_list"),
            ({"data": {"list": [None]}}, "record_schema", "invalid_record"),
            ({"data": {"list": [{"ltGmInfoCn": None, "wnRnk": 0}]}}, "record_schema", "invalid_number_info"),
            ({"data": {"list": [{"ltGmInfoCn": "1:123456", "wnRnk": None}]}}, "record_schema", "invalid_rank_type"),
            ({"data": {"list": [{"ltGmInfoCn": "1:123456", "wnRnk": ""}]}}, "record_schema", "invalid_rank_format"),
            ({"data": {"list": [{"ltGmInfoCn": "private-group:123456", "wnRnk": 0}]}}, "number_format", "invalid_group_or_number"),
        )
        for payload, stage, reason in cases:
            with self.subTest(stage=stage, reason=reason):
                with self.assertLogs("win720", level="ERROR") as logs:
                    result = self.query(payload)
                self.assertTrue(result["detail_error"])
                self.assertEqual(result["detail_error_code"], "detail_error")
                self.assertEqual(result["detail_failure_stage"], stage)
                self.assertEqual(result["detail_failure_reason"], reason)
                self.assertIn("stage=" + stage, "\n".join(logs.output))
                self.assertIn("reason=" + reason, "\n".join(logs.output))
                self.assertNotIn("123456", "\n".join(logs.output))
                self.assertNotIn("private-group", "\n".join(logs.output))

    def test_unknown_field_names_values_and_actual_rank_are_excluded(self):
        payload = {
            "private-dynamic-key": "private-top-value",
            "data": {
                "private-nested-key": "private-nested-value",
                "list": [{
                    "private-record-key": "private-record-value",
                    "ltGmInfoCn": "private-number-information",
                    "wnRnk": 837261,
                }, None],
            },
        }
        summary = json.dumps(self.game._detail_response_schema(payload), sort_keys=True)
        with self.assertLogs("win720", level="ERROR") as logs:
            result = self.query(payload)
        output = summary + "\n".join(logs.output)
        for private_text in (
            "private-dynamic-key", "private-top-value", "private-nested-key", "private-nested-value",
            "private-record-key", "private-record-value", "private-number-information", "837261",
            "private-order-identifier",
        ):
            self.assertNotIn(private_text, output)
        self.assertIn('"length": 2', output)
        self.assertIn('"ltGmInfoCn": {"type": "string"}', output)
        self.assertIn('"wnRnk": {"type": "number"}', output)
        self.assertEqual(result["detail_failure_reason"], "invalid_record")

    def test_null_empty_array_and_scalar_types_have_no_values(self):
        self.assertEqual(self.game._detail_response_schema(None), {"type": "null"})
        self.assertEqual(self.game._detail_response_schema([]), {"type": "array", "length": 0})
        self.assertEqual(self.game._detail_response_schema("private-value"), {"type": "string"})
        self.assertEqual(self.game._detail_response_schema(True), {"type": "boolean"})
        self.assertEqual(self.game._detail_response_schema(837261), {"type": "number"})
        self.assertEqual(self.game._detail_response_schema(object()), {"type": "other"})

    def test_empty_list_diagnostic_records_only_array_length(self):
        with self.assertLogs("win720", level="ERROR") as logs:
            result = self.query({"data": {"list": [], "private-dynamic-key": "private-value"}})
        output = "\n".join(logs.output)
        self.assertEqual(result["detail_failure_reason"], "empty_list")
        self.assertIn('"list": {"length": 0, "type": "array"}', output)
        self.assertNotIn("private-dynamic-key", output)
        self.assertNotIn("private-value", output)

    def test_valid_legacy_details_do_not_confirm_an_incomplete_ledger(self):
        for rank in (0, "0", 7, "7"):
            with self.subTest(rank=rank):
                result = self.query({"data": {"list": [{"ltGmInfoCn": "1:123456", "wnRnk": rank}]}})
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error_code"], "incomplete_result")
                self.assertEqual(len(result["win720_details"]), 1)
                for field in ("detail_error", "detail_error_code", "detail_failure_stage", "detail_failure_reason"):
                    self.assertNotIn(field, result)


if __name__ == "__main__":
    unittest.main()
