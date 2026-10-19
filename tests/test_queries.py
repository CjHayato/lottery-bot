"""Read-only lottery query regressions; no credentials or network are used."""

import copy
import json
import unittest
from unittest.mock import MagicMock, call, patch

import requests

import auth
import common
import lotto645
import win720


def json_response(payload=None, status=200, body=None):
    response = requests.Response()
    response.status_code = status
    response._content = body if body is not None else json.dumps(payload).encode("utf-8")
    response.encoding = "utf-8"
    response.url = "https://example.invalid/private-order?token=private-token"
    return response


class JSONRequestTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        sleep = patch("common.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def query(self):
        return common.get_json_response(
            self.client, "https://example.invalid/query?token=private-token",
            headers={"Authorization": "private-secret"}, params={"order": "private-order"},
        )

    def test_valid_json_keeps_request_arguments_and_never_posts(self):
        self.client.get.return_value = json_response({"data": {"list": []}})
        self.assertEqual(self.query(), {"data": {"list": []}})
        self.client.get.assert_called_once_with(
            "https://example.invalid/query?token=private-token",
            headers={"Authorization": "private-secret"}, params={"order": "private-order"},
        )
        self.client.post.assert_not_called()
        self.sleep.assert_not_called()

    def test_transient_timeout_connection_server_and_invalid_json_recover(self):
        for failure in (
            requests.ConnectTimeout("private-token"),
            requests.ReadTimeout("private-token"),
            requests.ConnectionError("private-token"),
            json_response({}, status=503),
            json_response(body=b"<html>private-token</html>"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.client.reset_mock()
                self.sleep.reset_mock()
                self.client.get.side_effect = [failure, failure, json_response({"ok": True})]
                self.assertEqual(self.query(), {"ok": True})
                self.assertEqual(self.client.get.call_count, 3)
                self.assertEqual(self.sleep.call_args_list, [call(2), call(4)])

    def test_persistent_retryable_failure_uses_fixed_code_without_private_data(self):
        for failure, code in (
            (requests.Timeout("private-token private-secret private-order"), "timeout"),
            (requests.ConnectionError("private-token"), "connection_error"),
            (json_response({}, status=502), "http_error"),
            (json_response(body=b"private-token private-secret private-order"), "invalid_json"),
        ):
            with self.subTest(code=code):
                self.client.reset_mock()
                self.sleep.reset_mock()
                self.client.get.side_effect = [failure] * 3
                with self.assertLogs("common", level="ERROR") as logs:
                    with self.assertRaises(common.LotteryQueryError) as caught:
                        self.query()
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(str(caught.exception), code)
                self.assertEqual(self.client.get.call_count, 3)
                self.assertEqual(self.sleep.call_args_list, [call(2), call(4)])
                for secret in ("private-token", "private-secret", "private-order"):
                    self.assertNotIn(secret, "\n".join(logs.output))

    def test_unauthorized_forbidden_and_other_client_errors_stop_immediately(self):
        for status, code in ((401, "unauthorized"), (403, "forbidden"), (404, "http_error")):
            with self.subTest(status=status):
                self.client.reset_mock()
                self.client.get.return_value = json_response({}, status=status)
                with self.assertLogs("common", level="ERROR"):
                    with self.assertRaises(common.LotteryQueryError) as caught:
                        self.query()
                self.assertEqual(caught.exception.code, code)
                self.client.get.assert_called_once()
                self.sleep.assert_not_called()

    def test_non_object_json_is_not_a_success(self):
        for payload in (None, [], "private-token", 7, True):
            with self.subTest(payload=payload):
                self.client.reset_mock()
                self.client.get.return_value = json_response(payload)
                with self.assertLogs("common", level="ERROR"):
                    with self.assertRaises(common.LotteryQueryError) as caught:
                        self.query()
                self.assertEqual(caught.exception.code, "invalid_response")
                self.client.get.assert_called_once()

    def test_api_error_with_http_200_is_not_a_success(self):
        self.client.get.return_value = json_response({
            "resultCode": "private-result-code", "resultMsg": "private-message", "data": {"list": []},
        })
        with self.assertLogs("common", level="ERROR") as logs:
            with self.assertRaises(common.LotteryQueryError) as caught:
                self.query()
        self.assertEqual(caught.exception.code, "api_error")
        self.client.get.assert_called_once()
        self.sleep.assert_not_called()
        self.assertNotIn("private-result-code", "\n".join(logs.output))
        self.assertNotIn("private-message", "\n".join(logs.output))

    def test_explicit_amounts_include_zero_and_correctly_grouped_commas(self):
        for value, expected in ((0, 0), ("0", 0), (" 5000 ", 5000), ("1,234,567", 1234567)):
            with self.subTest(value=value):
                self.assertEqual(common.parse_amount(value), expected)

    def test_unknown_or_malformed_amount_is_never_zero(self):
        for value in (None, "", "-", "-1", -1, True, 1.5, "1.5", {}, [], "1,23", "１"):
            with self.subTest(value=value):
                with self.assertRaises(common.LotteryQueryError) as caught:
                    common.parse_amount(value)
                self.assertEqual(caught.exception.code, "invalid_amount")


class WinningQueryTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        singleton = patch.object(auth.HttpClientSingleton, "get_instance", return_value=self.client)
        singleton.start()
        self.addCleanup(singleton.stop)
        sleep = patch("common.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        self.auth_ctrl = auth.AuthController()
        self.games = ((lotto645.Lotto645(), "lotto_details", "LO40"), (win720.Win720(), "win720_details", "LP72"))
        self.item = {
            "ltEpsdView": "1200회", "ltWnAmt": "5000", "eltOrdrDt": "20261012",
            "epsdRflDt": "20261017", "ltGdsCd": "LO40", "ltEpsd": "1200",
            "gmInfo": "private-barcode", "ntslOrdrNo": "private-order",
        }

    def tearDown(self):
        self.client.post.assert_not_called()

    @staticmethod
    def detail_for(field):
        if field == "lotto_details":
            return {"data": {"ticket": {
                "game_dtl": [{"rank": "5", "num": [1, 2, 3, 4, 5, 6]}], "win_num": [1, 2, 3, 8, 9, 10],
            }}}
        return {"data": {"list": [{"ltGmInfoCn": "1:123456", "wnRnk": "7"}]}}

    def reset(self):
        self.client.reset_mock()
        self.client.get.side_effect = None
        self.sleep.reset_mock()

    def test_success_preserves_existing_result_fields_and_first_record(self):
        for game, field, product in self.games:
            with self.subTest(product=product):
                self.reset()
                self.client.get.side_effect = [
                    json_response({"data": {"list": [self.item, {"ltWnAmt": None}]}}),
                    json_response(self.detail_for(field)),
                ]
                result = game.check_winning(self.auth_ctrl)
                self.assertEqual(result["status"], "success" if product == "LO40" else "error")
                self.assertEqual(result["round"], "1200")
                self.assertEqual(result["money"], "5,000 원")
                self.assertEqual(result["purchased_date"], "20261012")
                self.assertEqual(result["winning_date"], "20261017")
                self.assertEqual(len(result[field]), 1)
                self.assertNotIn("detail_error", result)
                if product == "LO40":
                    self.assertNotIn("error_code", result)
                else:
                    self.assertEqual(result["error_code"], "incomplete_result")
                self.assertEqual(self.client.get.call_count, 2)
                request = self.client.get.call_args_list[0]
                self.assertEqual(request.kwargs["params"]["ltGdsCd"], product)
                self.assertEqual(request.kwargs["headers"]["requestMenuUri"], "/mypage/mylotteryledger")
                self.assertEqual(request.kwargs["headers"]["AJAX"], "true")
                self.assertEqual(request.kwargs["headers"]["Referer"], "https://www.dhlottery.co.kr/mypage/mylotteryledger")
                self.assertNotIn("Origin", request.kwargs["headers"])

    def test_only_a_validated_empty_list_is_no_data(self):
        for game, _, product in self.games:
            with self.subTest(product=product):
                self.reset()
                self.client.get.return_value = json_response({"data": {"list": []}})
                self.assertEqual(game.check_winning(self.auth_ctrl), {"status": "no_data"})
                self.client.get.assert_called_once()

    def test_missing_null_or_non_list_data_is_a_failure(self):
        for game, _, product in self.games:
            for payload in ({}, {"data": None}, {"data": {}}, {"data": {"list": None}},
                            {"data": {"list": {}}}, {"data": []}, {"data": {"list": [None]}}):
                with self.subTest(product=product, payload=payload):
                    self.reset()
                    self.client.get.return_value = json_response(payload)
                    with self.assertLogs(level="ERROR"):
                        result = game.check_winning(self.auth_ctrl)
                    self.assertEqual(result, {"status": "error", "error_code": "invalid_response"})
                    self.client.get.assert_called_once()

    def test_missing_null_or_invalid_amount_is_not_a_losing_result(self):
        for game, _, product in self.games:
            for value in (None, "", "unknown", True, -1, "1.5"):
                with self.subTest(product=product, amount=value):
                    self.reset()
                    item = {**self.item, "ltWnAmt": value}
                    self.client.get.return_value = json_response({"data": {"list": [item]}})
                    with self.assertLogs(level="ERROR"):
                        result = game.check_winning(self.auth_ctrl)
                    self.assertEqual(result, {"status": "error", "error_code": "invalid_amount"})
                    self.assertNotIn("money", result)
                    self.client.get.assert_called_once()
            self.reset()
            item = {key: value for key, value in self.item.items() if key != "ltWnAmt"}
            self.client.get.return_value = json_response({"data": {"list": [item]}})
            with self.assertLogs(level="ERROR"):
                self.assertEqual(game.check_winning(self.auth_ctrl)["error_code"], "invalid_amount")

    def test_explicit_zero_amount_remains_a_success(self):
        for game, field, product in self.games:
            with self.subTest(product=product):
                self.reset()
                item = {**self.item, "ltWnAmt": 0}
                data = {"list": [item]}
                if product == "LP72":
                    item.update(ltGdsCd="LP72", gmInfo="1:123456", ltWnResult="낙첨")
                    data["total"] = 1
                self.client.get.side_effect = [
                    json_response({"data": data}),
                    json_response(self.detail_for(field)),
                ]
                result = game.check_winning(self.auth_ctrl)
                self.assertEqual(result["status"], "success")
                self.assertEqual(result["money"], "0 원")

    def test_bad_round_does_not_produce_a_complete_result(self):
        for game, _, product in self.games:
            for value in (None, "", {}, True):
                with self.subTest(product=product, round=value):
                    self.reset()
                    self.client.get.return_value = json_response({"data": {"list": [{**self.item, "ltEpsdView": value}]}})
                    with self.assertLogs(level="ERROR"):
                        self.assertEqual(game.check_winning(self.auth_ctrl), {"status": "error", "error_code": "invalid_response"})

    def test_unauthorized_timeout_and_html_cannot_become_no_data(self):
        for game, _, product in self.games:
            for failure, expected, count in (
                (json_response({}, status=401), "unauthorized", 1),
                (json_response({}, status=403), "forbidden", 1),
                (requests.ConnectTimeout("private-token"), "timeout", 3),
                (json_response(body=b"<html>private-token</html>"), "invalid_json", 3),
                (json_response({"resultCode": "UNAUTH", "data": {"list": []}}), "api_error", 1),
            ):
                with self.subTest(product=product, code=expected):
                    self.reset()
                    self.client.get.side_effect = [failure] * count
                    with self.assertLogs(level="ERROR"):
                        result = game.check_winning(self.auth_ctrl)
                    self.assertEqual(result, {"status": "error", "error_code": expected})
                    self.assertEqual(self.client.get.call_count, count)

    def test_failed_detail_preserves_round_and_money_but_marks_partial_failure(self):
        for game, field, product in self.games:
            with self.subTest(product=product):
                self.reset()
                self.client.get.side_effect = [json_response({"data": {"list": [self.item]}})] + [
                    requests.Timeout("private-token private-order private-barcode")
                ] * 3
                with self.assertLogs(level="ERROR") as logs:
                    result = game.check_winning(self.auth_ctrl)
                self.assertEqual(result["status"], "success" if product == "LO40" else "error")
                self.assertEqual(result["round"], "1200")
                self.assertEqual(result["money"], "5,000 원")
                self.assertEqual(result[field], [])
                self.assertTrue(result["detail_error"])
                self.assertEqual(result["error_code"], "detail_error")
                self.assertEqual(self.client.get.call_count, 4)
                for secret in ("private-token", "private-order", "private-barcode"):
                    self.assertNotIn(secret, "\n".join(logs.output))

    def test_malformed_detail_is_a_partial_failure(self):
        for game, field, product in self.games:
            for detail in ({}, {"data": None}, {"data": {"list": None}}, {"data": {"list": []}},
                           {"data": {"ticket": {"game_dtl": [], "win_num": []}}}):
                with self.subTest(product=product, detail=detail):
                    self.reset()
                    self.client.get.side_effect = [
                        json_response({"data": {"list": [self.item]}}), json_response(detail),
                    ]
                    with self.assertLogs(level="ERROR"):
                        result = game.check_winning(self.auth_ctrl)
                    self.assertTrue(result["detail_error"])
                    self.assertEqual(result["error_code"], "detail_error")
                    self.assertEqual(result[field], [])

    def test_original_query_headers_are_not_mutated(self):
        for game, _, product in self.games:
            with self.subTest(product=product):
                self.reset()
                original = copy.deepcopy(game._REQ_HEADERS)
                self.client.get.return_value = json_response({"data": {"list": []}})
                game.check_winning(self.auth_ctrl)
                self.assertEqual(game._REQ_HEADERS, original)


if __name__ == "__main__":
    unittest.main()
