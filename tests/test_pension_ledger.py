"""Use synthetic ledger records; block login, purchases, and all network access."""

import copy
import json
import re
import unittest
from unittest.mock import MagicMock, patch

import requests

import auth
import win720
from notification import Notification


class PensionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        for patcher in (
            patch.object(auth.HttpClientSingleton, "get_instance", return_value=self.client),
            patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden")),
            patch.object(auth.AuthController, "login", side_effect=AssertionError("Login forbidden")),
            patch.object(win720, "logger"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.auth_ctrl = auth.AuthController()
        self.game = win720.Win720()
        self.notification = Notification()
        delivery = patch.object(self.notification, "_send_message")
        self.delivery = delivery.start()
        self.addCleanup(delivery.stop)

    @staticmethod
    def record(group, number="012348", **updates):
        record = {
            "ltGdsCd": "LP72",
            "ltEpsdView": "444회",
            "ltEpsd": 444,
            "gmInfo": f"{group}:{number}",
            "ltWnResult": "낙첨",
            "ltWnAmt": 0,
            "wnRnk": None,
            "ntslOrdrNo": "synthetic-order",
            "eltOrdrDt": "2026-10-12",
            "epsdRflDt": "2026-10-15",
        }
        record.update(updates)
        return record

    def five_groups(self):
        return [self.record(group) for group in range(1, 6)]

    @staticmethod
    def response(payload):
        response = requests.Response()
        response.status_code = 200
        response.encoding = "utf-8"
        response._content = json.dumps(payload).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        return response

    def query(self, records, total=Ellipsis, include_total=True, fallback=None):
        data = {"list": records}
        if include_total:
            data["total"] = len(records) if total is Ellipsis else total
        self.client.get.reset_mock()
        self.client.get.side_effect = [
            self.response({"data": data}),
            self.response(fallback if fallback is not None else {"data": {"list": []}}),
        ]
        result = self.game.check_winning(self.auth_ctrl)
        self.client.post.assert_not_called()
        return result

    def assert_ledger_success(self, result, details=5, money="0 원"):
        self.assertEqual(result["status"], "success")
        self.assertEqual(result.get("detail_source"), "ledger")
        self.assertFalse(result.get("detail_error", False))
        self.assertEqual(result["round"], "444")
        self.assertEqual(result["money"], money)
        self.assertEqual(len(result["win720_details"]), details)
        self.client.get.assert_called_once()
        self.assertTrue(self.client.get.call_args.args[0].endswith("/selectMyLotteryledger.do"))

    def assert_not_clean_success(self, result):
        self.assertEqual(result.get("status"), "error",
                         "Unconfirmed ledger data must not summarize only the first ticket")
        self.assertNotEqual(result.get("detail_source"), "ledger")
        self.assert_notification_unconfirmed(result)

    def assert_notification_unconfirmed(self, result):
        self.delivery.reset_mock()
        self.notification.send_win720_winning_message(result, {"type": "none"})
        self.delivery.assert_called_once()
        message = self.delivery.call_args.args[1]
        self.assertIn("당첨 여부 미확인", message)
        self.assertNotIn("다음 기회", message)
        self.assertNotIn("당첨 되었습니다", message)
        self.assertNotIn("당첨되었습니다", message)

    def test_complete_five_group_losing_records_use_ledger_without_detail_request(self):
        result = self.query(self.five_groups())

        self.assert_ledger_success(result)
        self.assertEqual([detail["label"] for detail in result["win720_details"]],
                         ["1조", "2조", "3조", "4조", "5조"])
        self.assertEqual([detail["status"] for detail in result["win720_details"]], ["낙첨"] * 5)
        self.assertEqual(result["purchased_date"], "2026-10-12")
        self.assertEqual(result["winning_date"], "2026-10-15")

    def test_explicit_losing_records_allow_missing_null_or_empty_rank(self):
        for rank in ("missing", None, ""):
            with self.subTest(rank=rank):
                records = self.five_groups()
                for record in records:
                    if rank == "missing":
                        record.pop("wnRnk")
                    else:
                        record["wnRnk"] = rank
                result = self.query(records)
                self.assert_ledger_success(result)
                self.assertTrue(all(detail["status"] == "낙첨" for detail in result["win720_details"]))

    def test_total_may_be_integer_or_numeric_string(self):
        for total in (5, "5"):
            with self.subTest(total=total):
                self.assert_ledger_success(self.query(self.five_groups(), total=total))

    def test_winners_in_later_groups_are_included_and_amounts_are_summed(self):
        records = self.five_groups()
        records[2].update(ltWnResult="당첨", ltWnAmt="5,000", wnRnk="6")
        records[4].update(ltWnResult="당첨", ltWnAmt=1000, wnRnk=7)

        result = self.query(records)

        self.assert_ledger_success(result, money="6,000 원")
        self.assertEqual(result["win720_details"][2]["status"], "6등")
        self.assertEqual(result["win720_details"][4]["status"], "7등")
        self.assertEqual(result["win720_details"][2]["result"].count("["), 2)
        self.assertEqual(result["win720_details"][4]["result"].count("["), 1)

    def test_winner_without_rank_preserves_win_without_inventing_a_rank(self):
        for rank in ("missing", None, ""):
            with self.subTest(rank=rank):
                records = self.five_groups()
                records[4].update(ltWnResult="당첨", ltWnAmt=1000)
                if rank == "missing":
                    records[4].pop("wnRnk")
                else:
                    records[4]["wnRnk"] = rank
                result = self.query(records)
                self.assert_ledger_success(result, money="1,000 원")
                self.assertEqual(result["win720_details"][4]["status"], "당첨")
                self.assertNotIn("[", result["win720_details"][4]["result"])

    def test_previous_round_does_not_add_details_or_prize_money(self):
        records = self.five_groups()
        records.append(self.record(1, number="246801", ltEpsdView="443회", ltEpsd=443,
                                   ltWnResult="당첨", ltWnAmt=1000000, wnRnk=3))

        result = self.query(records)

        self.assert_ledger_success(result)
        self.assertTrue(all(re.sub(r"\D", "", detail["result"]) == "012348"
                            for detail in result["win720_details"]))

    def test_leading_zero_and_all_six_digits_are_preserved(self):
        result = self.query(self.five_groups())

        self.assert_ledger_success(result)
        for detail in result["win720_details"]:
            self.assertEqual(re.sub(r"\D", "", detail["result"]), "012348")

    def test_partial_page_keeps_existing_detail_api_fallback(self):
        fallback = {"data": {"list": [{"ltGmInfoCn": "4:246801", "wnRnk": "7"}]}}

        result = self.query(self.five_groups(), total=6, fallback=fallback)

        self.assertEqual(self.client.get.call_count, 2)
        self.assertTrue(self.client.get.call_args.args[0].endswith("/lottery720select.do"))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "incomplete_result")
        self.assertNotEqual(result.get("detail_source"), "ledger")
        self.assertEqual(len(result["win720_details"]), 1)
        self.assertEqual(result["win720_details"][0]["label"], "4조")
        self.assertEqual(result["win720_details"][0]["status"], "7등")
        self.assertEqual(re.sub(r"\D", "", result["win720_details"][0]["result"]), "246801")
        self.assert_notification_unconfirmed(result)

    def test_missing_or_invalid_total_cannot_produce_ledger_success(self):
        for total, include_total in ((None, False), (None, True), (True, True), (5.0, True),
                                     ("five", True), ("", True), (4, True), (6, True), (-1, True)):
            with self.subTest(total=total, include_total=include_total):
                result = self.query(self.five_groups(), total=total, include_total=include_total)
                self.assert_not_clean_success(result)
                self.assertEqual(self.client.get.call_count, 2)

    def test_unconfirmed_pending_or_unknown_result_cannot_become_losing_success(self):
        for outcome in ("미확인", "미추첨", "unknown", "", None):
            with self.subTest(outcome=outcome):
                records = self.five_groups()
                records[-1]["ltWnResult"] = outcome
                self.assert_not_clean_success(self.query(records))

    def test_later_pending_ticket_with_null_amount_remains_unconfirmed(self):
        fallback = {"data": {"list": [{"ltGmInfoCn": "1:246801", "wnRnk": "0"}]}}
        for status in ("미확인", "미추첨"):
            for total in (5, 6):
                with self.subTest(status=status, total=total):
                    records = self.five_groups()
                    records[-1].update(ltWnResult=status, ltWnAmt=None)
                    result = self.query(records, total=total, fallback=fallback)
                    self.assert_not_clean_success(result)
                    self.assertEqual(result["error_code"],
                                     "result_pending" if total == 5 else "incomplete_result")
                    self.assertEqual(self.client.get.call_count, 1 if total == 5 else 2)

    def test_empty_list_with_positive_total_is_an_invalid_response(self):
        for total in (1, "1"):
            with self.subTest(total=total):
                result = self.query([], total=total)
                self.assert_not_clean_success(result)
                self.assertEqual(result["error_code"], "invalid_response")
                self.client.get.assert_called_once()

    def test_empty_list_with_zero_total_still_means_no_purchase_records(self):
        result = self.query([], total=0)
        self.assertEqual(result["status"], "no_data")
        self.client.get.assert_called_once()

    def test_missing_required_fields_do_not_allow_partial_ledger_success(self):
        for field in ("gmInfo", "ltWnResult", "ltWnAmt", "ltGdsCd", "ltEpsdView"):
            with self.subTest(field=field):
                records = self.five_groups()
                records[-1].pop(field)
                result = self.query(records)
                self.assert_not_clean_success(result)

    def test_invalid_group_or_ticket_number_cannot_produce_ledger_success(self):
        for number_info in ("0:012348", "6:012348", "1:12348", "1:0123489", "1:01234x",
                            "1조 012348", "1:٠١٢٣٤٨", "", None, 12348):
            with self.subTest(number_info=number_info):
                records = self.five_groups()
                records[-1]["gmInfo"] = number_info
                self.assert_not_clean_success(self.query(records))

    def test_inconsistent_outcome_and_amount_cannot_produce_ledger_success(self):
        for outcome, amount in (("낙첨", 1000), ("당첨", 0), ("당첨", "0"),
                                ("당첨", -1), ("낙첨", None), ("낙첨", True),
                                ("낙첨", "invalid"), ("낙첨", "1,00")):
            with self.subTest(outcome=outcome, amount=amount):
                records = self.five_groups()
                records[-1].update(ltWnResult=outcome, ltWnAmt=amount)
                self.assert_not_clean_success(self.query(records))

    def test_invalid_explicit_winning_rank_cannot_produce_ledger_success(self):
        for rank in (True, -1, "invalid", [], {}):
            with self.subTest(rank=rank):
                records = self.five_groups()
                records[-1].update(ltWnResult="당첨", ltWnAmt=1000, wnRnk=rank)
                self.assert_not_clean_success(self.query(records))

    def test_unrecognized_numeric_winning_rank_does_not_invent_rank_or_highlights(self):
        for rank in (0, "0", 8, "8", 21, "21"):
            with self.subTest(rank=rank):
                records = self.five_groups()
                records[-1].update(ltWnResult="당첨", ltWnAmt=1000, wnRnk=rank)
                result = self.query(records)
                self.assert_ledger_success(result, money="1,000 원")
                self.assertEqual(result["win720_details"][-1]["status"], "당첨")
                self.assertNotIn("[", result["win720_details"][-1]["result"])

    def test_fallback_is_preserved_when_ledger_schema_is_unavailable(self):
        records = self.five_groups()
        for record in records:
            record.pop("gmInfo")
            record.pop("ltWnResult")
        fallback = {"data": {"list": [{"ltGmInfoCn": "2:246801", "wnRnk": "0"}]}}

        result = self.query(records, fallback=copy.deepcopy(fallback))

        self.assertEqual(self.client.get.call_count, 2)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "incomplete_result")
        self.assertFalse(result.get("detail_error", False))
        self.assertNotEqual(result.get("detail_source"), "ledger")
        self.assertEqual(result["win720_details"][0]["label"], "2조")
        self.assertEqual(result["win720_details"][0]["status"], "0등")
        self.assert_notification_unconfirmed(result)


if __name__ == "__main__":
    unittest.main()
