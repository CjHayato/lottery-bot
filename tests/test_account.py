"""Account/session checks with fake responses; no live account is accessed."""

import contextlib
import io
import json
import unittest
from unittest.mock import MagicMock, patch

import requests

import auth


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.session = requests.Session()
        self.addCleanup(self.client.session.close)
        singleton = patch.object(auth.HttpClientSingleton, "get_instance", return_value=self.client)
        singleton.start()
        self.addCleanup(singleton.stop)
        sleep = patch("common.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.controller = auth.AuthController()

    @staticmethod
    def response(payload=None, body=None, status=200):
        response = requests.Response()
        response.status_code = status
        response.url = "https://www.dhlottery.co.kr/mypage/selectUserMndp.do"
        response.encoding = "utf-8"
        response._content = (body if body is not None else json.dumps(payload)).encode("utf-8")
        response.headers["Content-Type"] = "text/html" if body is not None else "application/json"
        return response

    def test_logged_out_login_response_is_rejected_despite_session_cookie(self):
        self.client.session.cookies.set("JSESSIONID", "anonymous-session", domain="www.dhlottery.co.kr")
        self.client.post.return_value = self.response(body="<script>const isLoggedIn = false;</script>")

        with self.assertRaises(auth.AuthenticationError) as caught:
            self.controller._try_login({}, {})

        self.assertEqual(caught.exception.code, "not_authenticated")
        self.client.get.assert_not_called()

    def test_logged_in_page_accepts_native_session_without_cookie_copy(self):
        cookies = self.client.session.cookies
        cookies.set("WMONID", "analytics", domain=".dhlottery.co.kr")
        cookies.set("JSESSIONID", "authenticated-session", domain="www.dhlottery.co.kr")
        before = [(c.name, c.value, c.domain, c.path) for c in cookies]
        self.client.post.return_value = self.response(body="<script>const isLoggedIn = true;</script>")

        self.controller._try_login({}, {})

        self.assertEqual([(c.name, c.value, c.domain, c.path) for c in cookies], before)
        self.assertEqual(self.controller.get_current_session_id(), "authenticated-session")
        self.client.get.assert_not_called()

    def test_missing_post_marker_checks_main_page(self):
        self.client.post.return_value = self.response(body="redirecting")
        self.client.get.return_value = self.response(body="<script>const isLoggedIn = true;</script>")

        self.controller._try_login({}, {})

        self.assertEqual(self.client.get.call_args.args[0], "https://www.dhlottery.co.kr/main")

    def test_logged_out_main_page_is_rejected(self):
        self.client.post.return_value = self.response(body="redirecting")
        self.client.get.return_value = self.response(body="<script>const isLoggedIn = false;</script>")
        with self.assertRaises(auth.AuthenticationError):
            self.controller._try_login({}, {})

    def test_no_page_marker_requires_valid_protected_account_data(self):
        self.client.post.return_value = self.response(body="redirecting")
        self.client.get.side_effect = [
            self.response(body="<html>main without marker</html>"),
            self.response({"data": {"userMndp": {"totalAmt": 1200}}}),
        ]

        self.controller._try_login({}, {})

        self.assertEqual(self.client.get.call_count, 2)
        self.assertIn("selectUserMndp.do", self.client.get.call_args.args[0])

    def test_missing_marker_and_unauthorized_api_do_not_count_as_login(self):
        self.client.post.return_value = self.response(body="redirecting")
        self.client.get.side_effect = [self.response(body="main"), self.response({}, status=401)]
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(auth.AuthenticationError) as caught:
                self.controller._try_login({}, {})
        self.assertEqual(caught.exception.code, "unauthorized")

    def test_analytics_cookie_is_never_used_as_session_id(self):
        self.client.session.cookies.set("WMONID", "analytics", domain=".dhlottery.co.kr")
        self.assertEqual(self.controller.get_current_session_id(), "")

    def test_session_cookie_priority_is_independent_of_cookie_order(self):
        cookies = self.client.session.cookies
        cookies.set("WMONID", "analytics", domain=".dhlottery.co.kr")
        cookies.set("DHJSESSIONID", "secondary", domain=".dhlottery.co.kr")
        cookies.set("JSESSIONID", "primary", domain="www.dhlottery.co.kr")
        self.assertEqual(self.controller.get_current_session_id(), "primary")

    def test_game_cookie_sharing_is_explicit_and_keeps_existing_purchase_setup(self):
        cookies = self.client.session.cookies
        cookies.set("JSESSIONID", "authenticated-session", domain="www.dhlottery.co.kr")
        cookies.set("WMONID", "analytics", domain="www.dhlottery.co.kr")
        self.controller.prepare_game_session()
        self.assertEqual(cookies.get("JSESSIONID", domain=".dhlottery.co.kr"), "authenticated-session")
        self.assertEqual(cookies.get("WMONID", domain=".dhlottery.co.kr"), "analytics")

    def test_game_cookie_setup_cannot_turn_analytics_cookie_into_session(self):
        cookies = self.client.session.cookies
        cookies.set("WMONID", "analytics", domain="www.dhlottery.co.kr")
        self.controller.prepare_game_session()
        self.assertFalse(any(cookie.name == "JSESSIONID" for cookie in cookies))

    def test_current_balance_uses_official_six_field_formula(self):
        self.client.get.return_value = self.response({"data": {"userMndp": {
            "pntDpstAmt": "2,000", "pntTkmnyAmt": 100,
            "ncsblDpstAmt": 5000, "ncsblTkmnyAmt": 500,
            "csblDpstAmt": 1000, "csblTkmnyAmt": 400,
            "totalAmt": 999999,
        }}})
        self.assertEqual(self.controller.get_user_balance(strict=True), "7,000원")

    def test_zero_and_null_components_match_official_client(self):
        self.client.get.return_value = self.response({"data": {"userMndp": {
            "pntDpstAmt": 0, "pntTkmnyAmt": None,
            "ncsblDpstAmt": None, "ncsblTkmnyAmt": 0,
            "csblDpstAmt": 0, "csblTkmnyAmt": 0,
        }}})
        self.assertEqual(self.controller.get_user_balance(strict=True), "0원")

    def test_legacy_total_amount_formats_are_still_supported(self):
        for payload in ({"totalAmt": "1,234"}, {"data": {"totalAmt": 1234}},
                        {"data": {"userMndp": {"totalAmt": "1234"}}}):
            with self.subTest(payload=payload):
                self.client.get.return_value = self.response(payload)
                self.assertEqual(self.controller.get_user_balance(strict=True), "1,234원")

    def test_missing_balance_fields_are_not_silently_zero(self):
        for payload in ({}, {"data": None}, {"data": {"userMndp": None}},
                        {"data": {"userMndp": {}}}, {"data": {"unrelated": 10}}):
            with self.subTest(payload=payload), contextlib.redirect_stdout(io.StringIO()):
                self.client.get.return_value = self.response(payload)
                with self.assertRaises(auth.AccountQueryError) as caught:
                    self.controller.get_user_balance(strict=True)
                self.assertEqual(caught.exception.code, "invalid_response")

    def test_invalid_amounts_are_not_silently_zero(self):
        for invalid in (None, True, False, "", "not-an-amount", -1, 1.5, "12,34"):
            with self.subTest(invalid=invalid), contextlib.redirect_stdout(io.StringIO()):
                self.client.get.return_value = self.response({"totalAmt": invalid})
                with self.assertRaises(auth.AccountQueryError) as caught:
                    self.controller.get_user_balance(strict=True)
                self.assertEqual(caught.exception.code, "invalid_amount")

    def test_unauthorized_balance_is_explicit_and_not_retried(self):
        self.client.get.return_value = self.response({}, status=401)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(auth.AccountQueryError) as caught:
                self.controller.get_user_balance(strict=True)
        self.assertEqual(caught.exception.code, "unauthorized")
        self.client.get.assert_called_once()
        self.sleep.assert_not_called()

    def test_balance_failure_keeps_buying_callers_string_contract(self):
        self.client.get.return_value = self.response({}, status=401)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.controller.get_user_balance(), "정보 로드 실패 (로그 확인)")

    def test_api_error_envelope_is_not_balance_data(self):
        self.client.get.return_value = self.response({"resultCode": "AUTH_ERROR", "data": {"totalAmt": 0}})
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(auth.AccountQueryError) as caught:
                self.controller.get_user_balance(strict=True)
        self.assertEqual(caught.exception.code, "api_error")


if __name__ == "__main__":
    unittest.main()
