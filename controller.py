import os
import sys
import datetime
from dotenv import load_dotenv

import auth
import lotto645
import win720
import notification
import time


def _current_kst() -> datetime.datetime:
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(kst)


def _is_kst_monday(now: datetime.datetime = None) -> bool:
    now = now or _current_kst()
    return now.weekday() == 0


def _should_skip_purchase() -> bool:
    now = _current_kst()
    if _is_kst_monday(now):
        return False

    print(
        "[Info] 구매는 한국시간 월요일에만 실행합니다. "
        f"현재: {now.strftime('%Y.%m.%d %H:%M:%S')} KST. 구매를 건너뜁니다."
    )
    return True


def _is_purchase_success(response: dict) -> bool:
    result = response.get("result", {})
    return result.get("resultMsg", "FAILURE").upper() == "SUCCESS"


def _clean_env_value(name: str):
    value = os.environ.get(name)
    if value is None:
        return None

    value = value.strip()
    if not value or value.startswith("YOUR_"):
        return None

    return value


def _required_env_value(name: str) -> str:
    value = _clean_env_value(name)
    if value is None:
        raise ValueError(
            f"Missing required environment variable: {name}. "
            f"GitHub Actions Settings > Secrets and variables > Actions에 {name} secret을 추가해 주세요."
        )

    return value


def _purchase_count() -> int:
    raw_count = _required_env_value("COUNT")
    try:
        count = int(raw_count)
    except ValueError:
        raise ValueError("COUNT secret은 숫자로 입력해야 합니다. 예: 5")

    if count <= 0:
        raise ValueError("COUNT secret은 1 이상의 숫자로 입력해야 합니다.")

    return count


def _notification_target() -> dict:
    telegram_bot_token = _clean_env_value("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = _clean_env_value("TELEGRAM_CHAT_ID")
    if telegram_bot_token and telegram_chat_id:
        return {
            "type": "telegram",
            "bot_token": telegram_bot_token,
            "chat_id": telegram_chat_id,
            "sender_server": _clean_env_value("TELEGRAM_SENDER_SERVER") or "GitHub Actions lottery-bot",
        }

    slack_webhook_url = _clean_env_value("SLACK_WEBHOOK_URL")
    if slack_webhook_url:
        return {"type": "slack", "webhook_url": slack_webhook_url}

    discord_webhook_url = _clean_env_value("DISCORD_WEBHOOK_URL")
    if discord_webhook_url:
        return {"type": "discord", "webhook_url": discord_webhook_url}

    return {"type": "none"}


def _setup_and_login(purchase=False):
    load_dotenv(override=True)
    username = _required_env_value('USERNAME')
    password = _required_env_value('PASSWORD')
    notify_target = _notification_target()

    auth_ctrl = auth.AuthController()
    try:
        auth_ctrl.login(username, password)
    except Exception:
        try:
            notification.Notification().send_login_failure_message(notify_target)
        except Exception:
            print("[Error] Login failure notification could not be sent.")
        raise

    if purchase:
        auth_ctrl.prepare_game_session()
    return auth_ctrl, username, notify_target

def buy_lotto645(authCtrl: auth.AuthController, cnt: int, mode: str):
    lotto = lotto645.Lotto645()
    _mode = lotto645.Lotto645Mode[mode.upper()]
    response = lotto.buy_lotto645(authCtrl, cnt, _mode)
    response['balance'] = authCtrl.get_user_balance()
    return response

def check_winning_lotto645(authCtrl: auth.AuthController) -> dict:
    lotto = lotto645.Lotto645()
    item = lotto.check_winning(authCtrl)
    _add_check_balance(authCtrl, item)
    return item

def buy_win720(authCtrl: auth.AuthController, username: str):
    pension = win720.Win720()
    response = pension.buy_Win720(authCtrl, username)
    response['balance'] = authCtrl.get_user_balance()
    return response

def check_winning_win720(authCtrl: auth.AuthController) -> dict:
    pension = win720.Win720()
    item = pension.check_winning(authCtrl)
    _add_check_balance(authCtrl, item)
    return item


def _add_check_balance(auth_ctrl: auth.AuthController, item: dict) -> None:
    try:
        item['balance'] = auth_ctrl.get_user_balance(strict=True)
    except auth.AccountQueryError as error:
        code = notification.Notification.safe_error_code(error.code)
        item['balance_error'] = code
        item['balance'] = f"조회 실패 ({notification.Notification.error_reason(code)})"


def _check_failure_codes(response: dict) -> list:
    failures = []
    if notification.Notification.winning_status(response) == "error":
        failures.append("winning_" + notification.Notification.safe_error_code(response.get("error_code")))
    if response.get("detail_error"):
        failures.append("detail_" + notification.Notification.safe_error_code(response.get("detail_error_code", "detail_error")))
    if response.get("balance_error"):
        failures.append("balance_" + notification.Notification.safe_error_code(response['balance_error']))
    return failures


def _run_winning_checks(auth_ctrl: auth.AuthController, notify_target: dict, lottery_types: tuple) -> None:
    failures = []
    for index, lottery_type in enumerate(lottery_types):
        if index:
            time.sleep(10)
        checker = check_winning_lotto645 if lottery_type == 0 else check_winning_win720
        label = "lotto" if lottery_type == 0 else "win720"
        try:
            response = checker(auth_ctrl)
            if not isinstance(response, dict):
                raise ValueError("Invalid winning check result")
        except Exception:
            # A failed query must never be presented as a losing ticket. Continue
            # the other lottery check, then fail the workflow with safe codes.
            response = {"status": "error", "error_code": "check_error", "balance": "확인불가"}
        try:
            send_message(0, lottery_type, response=response, notify_target=notify_target)
        except Exception:
            failures.append(f"{label}:notification_error")
        failures.extend(f"{label}:{code}" for code in _check_failure_codes(response))
    if failures:
        raise RuntimeError("Check Winning failed: " + ", ".join(failures))

def send_message(mode: int, lottery_type: int, response: dict, notify_target: dict):
    notify = notification.Notification()

    if mode == 0:
        if lottery_type == 0:
            notify.send_lotto_winning_message(response, notify_target)
        else:
            notify.send_win720_winning_message(response, notify_target)
    elif mode == 1: 
        if lottery_type == 0:
            notify.send_lotto_buying_message(response, notify_target)
        else:
            notify.send_win720_buying_message(response, notify_target)

def check():
    auth_ctrl, _, notify_target = _setup_and_login()
    _run_winning_checks(auth_ctrl, notify_target, (0, 1))

def buy(): 
    load_dotenv(override=True) 
    if _should_skip_purchase():
        return

    count = _purchase_count()
    mode = "AUTO"

    auth_ctrl, username, notify_target = _setup_and_login(purchase=True)

    response = buy_lotto645(auth_ctrl, count, mode) 
    send_message(1, 0, response=response, notify_target=notify_target)
    if not _is_purchase_success(response):
        print("[Info] 로또 구매가 실패하여 연금복권 구매를 건너뜁니다.")
        return

    time.sleep(10)

    auth_ctrl.http_client.session.cookies.clear()
    auth_ctrl, username, notify_target = _setup_and_login(purchase=True)

    response = buy_win720(auth_ctrl, username) 
    send_message(1, 1, response=response, notify_target=notify_target)

def lotto_buy():
    load_dotenv(override=True)
    if _should_skip_purchase():
        return

    count = _purchase_count()
    auth_ctrl, _, notify_target = _setup_and_login(purchase=True)
    mode = "AUTO"
    
    response = buy_lotto645(auth_ctrl, count, mode)
    send_message(1, 0, response=response, notify_target=notify_target)

def win720_buy():
    if _should_skip_purchase():
        return

    auth_ctrl, username, notify_target = _setup_and_login(purchase=True)

    response = buy_win720(auth_ctrl, username)
    send_message(1, 1, response=response, notify_target=notify_target)

def lotto_check():
    auth_ctrl, _, notify_target = _setup_and_login()
    _run_winning_checks(auth_ctrl, notify_target, (0,))

def win720_check():
    auth_ctrl, _, notify_target = _setup_and_login()
    _run_winning_checks(auth_ctrl, notify_target, (1,))

def run():
    if len(sys.argv) < 2:
        print("Usage: python controller.py [buy|check]")
        return

    if sys.argv[1] == "buy":
        buy()
    elif sys.argv[1] == "check":
        check()
    elif sys.argv[1] == "buy_lotto":
        lotto_buy()
    elif sys.argv[1] == "buy_win720":
        win720_buy()
    elif sys.argv[1] == "check_lotto":
        lotto_check()
    elif sys.argv[1] == "check_win720":
        win720_check()
  

if __name__ == "__main__":
    run()
