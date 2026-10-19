"""Exercise the complete check command with an in-memory HTTP server substitute."""

import contextlib
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

import requests
from Crypto.PublicKey import RSA

import auth
import controller
import notification


class CheckIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = RSA.generate(1024)
        cls.rsa_payload = {"data": {"rsaModulus": format(key.n, "x"),
                                    "publicExponent": format(key.e, "x")}}

    def setUp(self):
        self.logged_in = True
        self.query_failure = False
        self.client = MagicMock()
        self.client.session = requests.Session()
        self.addCleanup(self.client.session.close)
        self.client.get.side_effect = self.get
        self.client.post.side_effect = self.post
        for patcher in (
            patch.object(auth.HttpClientSingleton, "get_instance", return_value=self.client),
            patch("controller.load_dotenv"),
            patch.dict(os.environ, {"USERNAME": "offline-user", "PASSWORD": "offline-password",
                                    "TELEGRAM_BOT_TOKEN": "offline-token", "TELEGRAM_CHAT_ID": "offline-chat"}, clear=True),
            patch("auth.time.sleep"),
            patch("common.time.sleep"),
            patch("controller.time.sleep"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        sender = patch.object(notification.Notification, "_send_message")
        self.sender = sender.start()
        self.addCleanup(sender.stop)

    @staticmethod
    def response(payload=None, body=None, status=200):
        response = requests.Response()
        response.status_code = status
        response.encoding = "utf-8"
        response.url = "https://www.dhlottery.co.kr/login/selectRsaModulus.do"
        response._content = (body if body is not None else json.dumps(payload)).encode("utf-8")
        response.headers["Content-Type"] = "text/html" if body is not None else "application/json"
        return response

    def post(self, url, **kwargs):
        self.assertEqual(url, "https://www.dhlottery.co.kr/login/securityLoginCheck.do")
        self.client.session.cookies.set("JSESSIONID", "offline-session", domain="www.dhlottery.co.kr")
        return self.response(body="<script>const isLoggedIn = " + str(self.logged_in).lower() + ";</script>")

    def get(self, url, headers=None, params=None):
        if url in ("https://www.dhlottery.co.kr/", "https://www.dhlottery.co.kr/login"):
            return self.response(body="<html>public page</html>")
        if url.endswith("/login/selectRsaModulus.do"):
            return self.response(self.rsa_payload)
        if url.endswith("/mypage/selectUserMndp.do"):
            if self.query_failure:
                return self.response({}, status=401)
            return self.response({"data": {"userMndp": {
                "pntDpstAmt": 1000, "pntTkmnyAmt": 0,
                "ncsblDpstAmt": 7000, "ncsblTkmnyAmt": 1000,
                "csblDpstAmt": 0, "csblTkmnyAmt": 0,
            }}})
        if url.endswith("/mypage/selectMyLotteryledger.do"):
            is_lotto = params["ltGdsCd"] == "LO40"
            if self.query_failure:
                if is_lotto:
                    return self.response(body="<html>not account data</html>")
                raise requests.ConnectTimeout("offline simulated timeout")
            return self.response({"data": {"list": [{
                "ltEpsdView": "1247회" if is_lotto else "333회",
                "ltWnAmt": "0" if is_lotto else "1000",
                "ltGdsCd": params["ltGdsCd"], "ltEpsd": "offline-round",
                "gmInfo": "offline-barcode", "ntslOrdrNo": "offline-order",
            }]}})
        if url.endswith("/mypage/lotto645TicketDetail.do"):
            return self.response({"data": {"ticket": {
                "game_dtl": [{"rank": "0", "num": ["1", "2", "3", "4", "5", "6"]}],
                "win_num": ["7", "8", "9", "10", "11", "12"],
            }}})
        if url.endswith("/mypage/lottery720select.do"):
            return self.response({"data": {"list": [{"ltGmInfoCn": "1:123456", "wnRnk": "7"}]}})
        raise AssertionError("Unexpected HTTP request in an offline test")

    def messages(self):
        return [entry.args[1] for entry in self.sender.call_args_list]

    def test_authenticated_check_sends_both_results_and_real_balance(self):
        controller.check()
        messages = self.messages()
        self.assertEqual(len(messages), 2)
        self.assertIn("1247회", messages[0])
        self.assertIn("다음 기회", messages[0])
        self.assertIn("333회", messages[1])
        self.assertIn("당첨 되었습니다", messages[1])
        for message in messages:
            self.assertIn("7,000원", message)
            self.assertNotIn("조회 실패", message)
        self.assertFalse(any(cookie.domain == ".dhlottery.co.kr"
                             for cookie in self.client.session.cookies))
        self.client.post.assert_called_once()

    def test_purchase_login_keeps_game_cookie_setup_without_making_a_purchase(self):
        controller._setup_and_login(purchase=True)
        self.assertEqual(self.client.session.cookies.get("JSESSIONID", domain=".dhlottery.co.kr"),
                         "offline-session")
        self.client.post.assert_called_once()
        self.assertIn("/login/securityLoginCheck.do", self.client.post.call_args.args[0])
        self.sender.assert_not_called()

    def test_reported_json_timeout_and_401_failures_are_not_losing_results(self):
        self.query_failure = True
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError) as caught:
                controller.check()
        messages = self.messages()
        self.assertEqual(len(messages), 2)
        for message in messages:
            self.assertIn("당첨 여부 미확인", message)
            self.assertIn("로그인 확인 필요", message)
            self.assertNotIn("다음 기회", message)
        self.assertIn("winning_invalid_json", str(caught.exception))
        self.assertIn("winning_timeout", str(caught.exception))
        self.assertIn("balance_unauthorized", str(caught.exception))
        self.client.post.assert_called_once()

    def test_rejected_login_notifies_once_and_prevents_account_queries(self):
        self.logged_in = False
        with self.assertRaises(auth.AuthenticationError):
            controller.check()
        self.assertEqual(len(self.messages()), 1)
        self.assertIn("로그인 확인 실패", self.messages()[0])
        self.assertTrue(all("/mypage/" not in entry.args[0]
                            for entry in self.client.get.call_args_list))
        self.client.post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
