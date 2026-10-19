"""Check outcomes and notifications, with all requests and delivery mocked."""

import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

import auth
import controller
from notification import Notification


def success(money="0 원", lottery_type=0):
    details_key = "lotto_details" if lottery_type == 0 else "win720_details"
    return {"status": "success", "round": "1240", "money": money,
            details_key: [], "balance": "5,000원"}


class WinningNotificationTests(unittest.TestCase):
    def setUp(self):
        self.notify = Notification()
        self.deliver = patch.object(self.notify, "_send_message").start()
        self.addCleanup(patch.stopall)

    def message(self, result, lottery_type=0):
        self.deliver.reset_mock()
        sender = self.notify.send_lotto_winning_message if lottery_type == 0 else self.notify.send_win720_winning_message
        sender(result, {"type": "none"})
        self.deliver.assert_called_once()
        return self.deliver.call_args.args[1]

    def test_only_a_valid_successful_zero_amount_is_a_losing_ticket(self):
        for lottery_type in (0, 1):
            for amount in ("0 원", "0원", "0", 0):
                with self.subTest(lottery_type=lottery_type, amount=amount):
                    message = self.message(success(amount, lottery_type), lottery_type)
                    self.assertIn("다음 기회에", message)
                    self.assertIn("5,000원", message)
            winning = self.message(success("5,000 원", lottery_type), lottery_type)
            self.assertIn("당첨 되었습니다", winning)
            self.assertNotIn("다음 기회에", winning)

    def test_unauthorized_response_never_claims_a_losing_ticket(self):
        for lottery_type in (0, 1):
            with self.subTest(lottery_type=lottery_type):
                message = self.message({"status": "error", "error_code": "unauthorized",
                                        "balance": "조회 실패 (로그인 확인 필요)"}, lottery_type)
                self.assertIn("당첨 조회 실패 (당첨 여부 미확인)", message)
                self.assertIn("로그인 확인 필요", message)
                self.assertNotIn("다음 기회에", message)

    def test_empty_purchase_list_is_distinct_from_error_and_losing(self):
        for lottery_type in (0, 1):
            message = self.message({"status": "no_data", "balance": "5,000원"}, lottery_type)
            self.assertIn("조회 기간 내 구매 내역 없음", message)
            self.assertNotIn("다음 기회에", message)
            self.assertNotIn("당첨 조회 실패", message)

    def test_missing_or_invalid_result_information_is_unconfirmed(self):
        cases = [
            {}, {"status": "success"},
            {"status": "success", "round": "1240"},
            {"status": "success", "money": "0 원"},
            {"round": "1240", "money": "0 원"},
            {"status": "success", "round": "", "money": "0 원"},
        ]
        cases.extend(success(amount) for amount in (None, "-", "invalid", "1234,567 원", -1, True, []))
        for lottery_type in (0, 1):
            for result in cases:
                with self.subTest(lottery_type=lottery_type, result=result):
                    message = self.message(result.copy(), lottery_type)
                    self.assertIn("당첨 여부 미확인", message)
                    self.assertNotIn("다음 기회에", message)

    def test_partial_detail_failure_is_visible_even_when_prize_amount_is_known(self):
        for lottery_type in (0, 1):
            result = success("5,000 원", lottery_type)
            result.update(detail_error=True, detail_error_code="timeout")
            message = self.message(result, lottery_type)
            self.assertIn("5,000 원", message)
            self.assertIn("당첨 되었습니다", message)
            self.assertIn("상세 정보 조회 실패", message)
            self.assertIn("응답 시간 초과", message)

    def test_balance_failure_is_separate_from_a_valid_losing_result(self):
        result = success()
        result.update(balance="조회 실패 (응답 시간 초과)", balance_error="timeout")
        message = self.message(result)
        self.assertIn("다음 기회에", message)
        self.assertIn("남은잔액 : 조회 실패 (응답 시간 초과)", message)
        self.assertNotIn("당첨 조회 실패", message)

    def test_unknown_error_codes_never_echo_their_contents(self):
        message = self.message({"status": "error", "error_code": "secret-query-token"})
        self.assertNotIn("secret-query-token", message)
        self.assertIn("응답 정보 누락 또는 형식 오류", message)

    def test_existing_number_details_are_retained(self):
        lotto = success()
        lotto["lotto_details"] = [{"label": "A", "status": "낙첨", "result": ["1", "✨2"]}]
        message = self.message(lotto)
        self.assertIn("A 낙첨", message)
        self.assertIn("[02]", message)
        pension = success(lottery_type=1)
        pension["win720_details"] = [{"label": "1조", "status": "0등", "result": "1 2 3 4 5 6"}]
        message = self.message(pension, 1)
        self.assertIn("1조 0등", message)
        self.assertIn("1 2 3 4 5 6", message)


class CheckControllerTests(unittest.TestCase):
    def setUp(self):
        self.auth_ctrl = MagicMock()
        self.setup = patch.object(controller, "_setup_and_login", return_value=(self.auth_ctrl, "user", {"type": "none"})).start()
        self.sleep = patch.object(controller.time, "sleep").start()
        self.delivery = patch.object(Notification, "_send_message").start()
        self.addCleanup(patch.stopall)

    def test_balance_errors_are_attached_without_discarding_winning_result(self):
        for class_name, wrapper in (("Lotto645", controller.check_winning_lotto645),
                                    ("Win720", controller.check_winning_win720)):
            module = controller.lotto645 if class_name == "Lotto645" else controller.win720
            original = success("5,000 원")
            self.auth_ctrl.get_user_balance.reset_mock()
            self.auth_ctrl.get_user_balance.side_effect = auth.AccountQueryError("timeout")
            with patch.object(module, class_name) as lottery:
                lottery.return_value.check_winning.return_value = original.copy()
                result = wrapper(self.auth_ctrl)
            self.auth_ctrl.get_user_balance.assert_called_once_with(strict=True)
            self.assertEqual(result["money"], "5,000 원")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["balance_error"], "timeout")
            self.assertEqual(result["balance"], "조회 실패 (응답 시간 초과)")

    def test_both_lotteries_notify_before_final_failure(self):
        first = {"status": "error", "error_code": "unauthorized", "balance": "확인불가"}
        with patch.object(controller, "check_winning_lotto645", return_value=first), \
             patch.object(controller, "check_winning_win720", return_value=success(lottery_type=1)) as pension:
            with self.assertRaisesRegex(RuntimeError, "lotto:winning_unauthorized"):
                controller.check()
        pension.assert_called_once_with(self.auth_ctrl)
        self.assertEqual(self.delivery.call_count, 2)
        self.assertIn("당첨 여부 미확인", self.delivery.call_args_list[0].args[1])
        self.assertIn("다음 기회에", self.delivery.call_args_list[1].args[1])

    def test_unexpected_query_error_continues_and_never_leaks_exception(self):
        with patch.object(controller, "check_winning_lotto645", side_effect=RuntimeError("private-url-and-cookie")), \
             patch.object(controller, "check_winning_win720", return_value={"status": "no_data"}) as pension:
            with self.assertRaises(RuntimeError) as caught:
                controller.check()
        pension.assert_called_once_with(self.auth_ctrl)
        self.assertEqual(self.delivery.call_count, 2)
        self.assertNotIn("private-url-and-cookie", str(caught.exception))
        self.assertIn("winning_check_error", str(caught.exception))

    def test_notification_error_does_not_skip_the_other_lottery(self):
        with patch.object(controller, "check_winning_lotto645", return_value=success()), \
             patch.object(controller, "check_winning_win720", return_value=success(lottery_type=1)) as pension, \
             patch.object(controller, "send_message", side_effect=[RuntimeError("secret-notification-url"), None]) as notify:
            with self.assertRaisesRegex(RuntimeError, "lotto:notification_error") as caught:
                controller.check()
        pension.assert_called_once()
        self.assertEqual(notify.call_count, 2)
        self.assertNotIn("secret-notification-url", str(caught.exception))

    def test_balances_and_details_make_single_checks_fail_after_notification(self):
        for entry, checker in ((controller.lotto_check, "check_winning_lotto645"),
                               (controller.win720_check, "check_winning_win720")):
            for fields, expected in (({"balance_error": "timeout"}, "balance_timeout"),
                                     ({"detail_error": True}, "detail_detail_error")):
                result = success()
                result.update(fields)
                self.delivery.reset_mock()
                with self.subTest(entry=entry.__name__, fields=fields), \
                     patch.object(controller, checker, return_value=result):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        entry()
                self.delivery.assert_called_once()

    def test_valid_success_and_no_purchase_data_finish_without_error(self):
        with patch.object(controller, "check_winning_lotto645", return_value=success()), \
             patch.object(controller, "check_winning_win720", return_value={"status": "no_data", "balance": "5,000원"}):
            controller.check()
        self.assertEqual(self.delivery.call_count, 2)
        self.sleep.assert_called_once_with(10)

    def test_cli_check_propagates_failure_for_actions_nonzero_exit(self):
        with patch.object(controller, "check_winning_lotto645", return_value={"status": "error", "error_code": "timeout"}), \
             patch.object(controller, "check_winning_win720", return_value={"status": "no_data"}), \
             patch.object(controller.sys, "argv", ["controller.py", "check"]):
            with self.assertRaises(RuntimeError):
                controller.run()

    def test_buy_balance_uses_existing_non_strict_call(self):
        self.auth_ctrl.get_user_balance.return_value = "5,000원"
        with patch.object(controller.lotto645, "Lotto645") as lottery:
            lottery.return_value.buy_lotto645.return_value = {"result": {"resultMsg": "SUCCESS"}}
            result = controller.buy_lotto645(self.auth_ctrl, 1, "AUTO")
        lottery.return_value.buy_lotto645.assert_called_once()
        self.auth_ctrl.get_user_balance.assert_called_once_with()
        self.assertEqual(result["balance"], "5,000원")


class LoginFailureNotificationTests(unittest.TestCase):
    def test_login_failure_notifies_once_and_preserves_original_exception(self):
        for delivery_error in (None, RuntimeError("secret-notification-url")):
            original = RuntimeError("safe-login-error")
            with self.subTest(delivery_error=bool(delivery_error)), \
                 patch.object(controller, "load_dotenv"), \
                 patch.dict(controller.os.environ, {"USERNAME": "test-user", "PASSWORD": "test-pass"}, clear=True), \
                 patch.object(controller.auth, "AuthController") as authentication, \
                 patch.object(Notification, "_send_message", side_effect=delivery_error) as delivery, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                authentication.return_value.login.side_effect = original
                with self.assertRaises(RuntimeError) as caught:
                    controller._setup_and_login()
                self.assertIs(caught.exception, original)
                delivery.assert_called_once()
                self.assertIn("로그인 확인 실패, 당첨 여부 미확인", delivery.call_args.args[1])
                self.assertNotIn("secret-notification-url", output.getvalue())

    def test_missing_credentials_still_raise_without_sending_login_message(self):
        with patch.object(controller, "load_dotenv"), \
             patch.dict(controller.os.environ, {}, clear=True), \
             patch.object(Notification, "_send_message") as delivery:
            with self.assertRaisesRegex(ValueError, "USERNAME"):
                controller._setup_and_login()
        delivery.assert_not_called()


if __name__ == "__main__":
    unittest.main()
