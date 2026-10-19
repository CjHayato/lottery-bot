"""Authentication regression tests; all HTTP requests are mocked."""

import contextlib
import io
import json
import unittest
from unittest.mock import MagicMock, call, patch

import requests
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

import auth


class AuthControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = RSA.generate(1024)
        cls.key_data = {
            "rsaModulus": format(cls.private_key.n, "x"),
            "publicExponent": format(cls.private_key.e, "x"),
        }

    def setUp(self):
        self.http_client = MagicMock()
        singleton = patch.object(
            auth.HttpClientSingleton, "get_instance", return_value=self.http_client
        )
        singleton.start()
        self.addCleanup(singleton.stop)
        sleep = patch("auth.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.controller = auth.AuthController()

    @staticmethod
    def response(body=b"", content_type="application/json", url=None):
        response = requests.Response()
        response.status_code = 200
        response._content = body
        response.headers["Content-Type"] = content_type
        response.url = url or "https://www.dhlottery.co.kr/login/selectRsaModulus.do"
        response.encoding = "utf-8"
        return response

    def json_response(self, payload):
        return self.response(json.dumps(payload).encode("utf-8"))

    def serve_rsa_responses(self, responses):
        responses = iter(responses)

        def get(url, **kwargs):
            if url == "https://www.dhlottery.co.kr/login/selectRsaModulus.do":
                response = next(responses)
                if isinstance(response, BaseException):
                    raise response
                return response
            return self.response()

        self.http_client.get.side_effect = get

    def rsa_request_count(self):
        return sum(
            entry.args[0]
            == "https://www.dhlottery.co.kr/login/selectRsaModulus.do"
            for entry in self.http_client.get.call_args_list
        )

    def test_flat_and_nested_rsa_responses_remain_supported(self):
        for payload in (self.key_data, {"data": self.key_data}):
            with self.subTest(payload_shape=list(payload)):
                self.http_client.get.return_value = self.json_response(payload)
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    actual = self.controller._get_rsa_key()
                self.assertEqual(
                    actual,
                    (self.key_data["rsaModulus"], self.key_data["publicExponent"]),
                )
                self.assertNotIn(self.key_data["rsaModulus"], output.getvalue())

    def test_rsa_request_keeps_existing_endpoint_and_ajax_headers(self):
        self.http_client.get.return_value = self.json_response(self.key_data)
        original_headers = self.controller._REQ_HEADERS.copy()

        self.controller._get_rsa_key()

        request = self.http_client.get.call_args
        self.assertEqual(
            request.args,
            ("https://www.dhlottery.co.kr/login/selectRsaModulus.do",),
        )
        headers = request.kwargs["headers"]
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(
            headers["Referer"],
            "https://www.dhlottery.co.kr/login",
        )
        self.assertNotIn("Upgrade-Insecure-Requests", headers)
        self.assertEqual(self.controller._REQ_HEADERS, original_headers)
        self.http_client.post.assert_not_called()

    def test_non_json_response_uses_retryable_error_and_preserves_cause(self):
        response = self.response(b"<html>Maintenance</html>", "text/html")
        parse_error = ValueError("JSON decoding failed")
        response.json = MagicMock(side_effect=parse_error)
        self.http_client.get.return_value = response

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(auth.RSAKeyResponseError) as caught:
                self.controller._get_rsa_key()

        self.assertIs(caught.exception.__cause__, parse_error)

    def test_non_object_json_responses_are_retryable(self):
        for payload in (None, [], [self.key_data], "not an object", 17, True):
            with self.subTest(payload=payload):
                self.http_client.get.return_value = self.json_response(payload)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(auth.RSAKeyResponseError):
                        self.controller._get_rsa_key()

    def test_missing_null_or_non_string_key_fields_are_retryable(self):
        invalid_payloads = [
            {},
            {"rsaModulus": self.key_data["rsaModulus"]},
            {"publicExponent": self.key_data["publicExponent"]},
            {"data": None},
            {"data": []},
            {"data": {"rsaModulus": self.key_data["rsaModulus"]}},
        ]
        for field in self.key_data:
            for value in (None, "", 65537, [], {}):
                invalid_payloads.append({**self.key_data, field: value})

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.http_client.get.return_value = self.json_response(payload)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(auth.RSAKeyResponseError):
                        self.controller._get_rsa_key()

    def test_non_hex_key_fields_are_retryable(self):
        for field in self.key_data:
            with self.subTest(field=field):
                payload = {**self.key_data, field: "not-hexadecimal"}
                self.http_client.get.return_value = self.json_response(payload)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(auth.RSAKeyResponseError):
                        self.controller._get_rsa_key()

    def test_invalid_rsa_parameters_are_retryable_and_preserve_cause(self):
        for field, value in (
            ("rsaModulus", "0"),
            ("publicExponent", "0"),
            ("publicExponent", "1"),
            ("publicExponent", "2"),
        ):
            with self.subTest(field=field, value=value):
                payload = {**self.key_data, field: value}
                self.http_client.get.return_value = self.json_response(payload)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(auth.RSAKeyResponseError) as caught:
                        self.controller._get_rsa_key()
                self.assertIsNotNone(caught.exception.__cause__)

    def test_bad_response_then_valid_response_recovers_before_login(self):
        self.serve_rsa_responses(
            [
                self.response(b"<html>Temporary failure</html>", "text/html"),
                self.json_response({"data": self.key_data}),
            ]
        )
        with patch.object(self.controller, "_try_login") as try_login:
            with contextlib.redirect_stdout(io.StringIO()):
                self.controller.login("test-user", "test-password")

        self.assertEqual(self.rsa_request_count(), 2)
        self.sleep.assert_called_once_with(2)
        try_login.assert_called_once()
        submitted = try_login.call_args.args[1]
        self.assertEqual(submitted["inpUserId"], "test-user")
        cipher = PKCS1_v1_5.new(self.private_key)
        self.assertEqual(
            cipher.decrypt(bytes.fromhex(submitted["userId"]), b"invalid"),
            b"test-user",
        )
        self.assertEqual(
            cipher.decrypt(bytes.fromhex(submitted["userPswdEncn"]), b"invalid"),
            b"test-password",
        )

    def test_persistent_bad_response_stops_after_five_attempts(self):
        self.serve_rsa_responses(
            [self.response(b"<html>Unavailable</html>", "text/html") for _ in range(5)]
        )
        with patch.object(self.controller, "_try_login") as try_login:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(auth.RSAKeyResponseError) as caught:
                    self.controller.login("test-user", "test-password")

        self.assertEqual(self.rsa_request_count(), 5)
        self.assertEqual(self.sleep.call_args_list, [call(2), call(4), call(8), call(16)])
        self.assertIsNotNone(caught.exception.__cause__)
        try_login.assert_not_called()
        self.http_client.post.assert_not_called()

    def test_request_exception_is_still_retried(self):
        self.serve_rsa_responses(
            [requests.ConnectionError("temporary connection failure"), self.json_response(self.key_data)]
        )
        with patch.object(self.controller, "_try_login") as try_login:
            with contextlib.redirect_stdout(io.StringIO()):
                self.controller.login("test-user", "test-password")

        self.assertEqual(self.rsa_request_count(), 2)
        self.sleep.assert_called_once_with(2)
        try_login.assert_called_once()

    def test_persistent_request_exception_re_raises_original_after_five_attempts(self):
        connection_error = requests.ConnectionError("temporary connection failure")
        self.http_client.get.side_effect = connection_error

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(requests.ConnectionError) as caught:
                self.controller.login("test-user", "test-password")

        self.assertIs(caught.exception, connection_error)
        self.assertEqual(self.http_client.get.call_count, 5)
        self.assertEqual(self.sleep.call_args_list, [call(2), call(4), call(8), call(16)])
        self.http_client.post.assert_not_called()

    def test_unexpected_value_error_is_not_retried(self):
        error = ValueError("unrelated programming error")
        self.http_client.get.side_effect = error

        with self.assertRaises(ValueError) as caught:
            self.controller.login("test-user", "test-password")

        self.assertIs(caught.exception, error)
        self.http_client.get.assert_called_once()
        self.sleep.assert_not_called()
        self.http_client.post.assert_not_called()

    def test_rsa_failure_diagnostics_exclude_body_query_cookies_and_credentials(self):
        secrets = (
            "private-body-secret",
            "private-query-secret",
            "private-cookie-secret",
            "private-user-secret",
            "private-password-secret",
            "private-url-user",
            "private-url-password",
        )
        response = self.response(
            b"<html>private-body-secret</html>",
            "text/html; charset=UTF-8",
            "https://private-url-user:private-url-password@www.dhlottery.co.kr/"
            "login/selectRsaModulus.do?token=private-query-secret",
        )
        response.headers["Set-Cookie"] = "session=private-cookie-secret"
        response.cookies.set("session", "private-cookie-secret")
        self.serve_rsa_responses([response] * 5)

        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(auth.RSAKeyResponseError) as caught:
                self.controller.login("private-user-secret", "private-password-secret")

        diagnostics = output.getvalue() + str(caught.exception)
        for secret in secrets:
            self.assertNotIn(secret, diagnostics)
        self.assertIn("200", diagnostics)
        self.assertIn("text/html", diagnostics)
        self.assertIn("www.dhlottery.co.kr", diagnostics)
        self.assertIn("/login/selectRsaModulus.do", diagnostics)
        self.assertIn(str(len(response.content)), diagnostics)
        self.assertIn("body_kind", diagnostics)
        self.assertIn("response_bytes", diagnostics)

    def test_transport_error_logs_do_not_include_request_exception_secrets(self):
        error = requests.ConnectionError(
            "GET https://www.dhlottery.co.kr/?token=private-transport-token "
            "Cookie: private-transport-cookie"
        )
        self.http_client.get.side_effect = error

        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(requests.ConnectionError):
                self.controller.login("test-user", "test-password")

        self.assertNotIn("private-transport-token", output.getvalue())
        self.assertNotIn("private-transport-cookie", output.getvalue())


if __name__ == "__main__":
    unittest.main()
