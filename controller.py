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


def _setup_and_login():
    load_dotenv(override=True)
    username = _required_env_value('USERNAME')
    password = _required_env_value('PASSWORD')
    notify_target = _notification_target()

    auth_ctrl = auth.AuthController()
    auth_ctrl.login(username, password)

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
    item['balance'] = authCtrl.get_user_balance()
    return item

def buy_win720(authCtrl: auth.AuthController, username: str):
    pension = win720.Win720()
    response = pension.buy_Win720(authCtrl, username)
    response['balance'] = authCtrl.get_user_balance()
    return response

def check_winning_win720(authCtrl: auth.AuthController) -> dict:
    pension = win720.Win720()
    item = pension.check_winning(authCtrl)
    item['balance'] = authCtrl.get_user_balance()
    return item

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

    response = check_winning_lotto645(auth_ctrl)
    send_message(0, 0, response=response, notify_target=notify_target)

    time.sleep(10)
    
    response = check_winning_win720(auth_ctrl)
    send_message(0, 1, response=response, notify_target=notify_target)

def buy(): 
    load_dotenv(override=True) 
    if _should_skip_purchase():
        return

    count = _purchase_count()
    mode = "AUTO"

    auth_ctrl, username, notify_target = _setup_and_login()

    response = buy_lotto645(auth_ctrl, count, mode) 
    send_message(1, 0, response=response, notify_target=notify_target)
    if not _is_purchase_success(response):
        print("[Info] 로또 구매가 실패하여 연금복권 구매를 건너뜁니다.")
        return

    time.sleep(10)

    auth_ctrl.http_client.session.cookies.clear()
    auth_ctrl, username, notify_target = _setup_and_login()

    response = buy_win720(auth_ctrl, username) 
    send_message(1, 1, response=response, notify_target=notify_target)

def lotto_buy():
    load_dotenv(override=True)
    if _should_skip_purchase():
        return

    count = _purchase_count()
    auth_ctrl, _, notify_target = _setup_and_login()
    mode = "AUTO"
    
    response = buy_lotto645(auth_ctrl, count, mode)
    send_message(1, 0, response=response, notify_target=notify_target)

def win720_buy():
    if _should_skip_purchase():
        return

    auth_ctrl, username, notify_target = _setup_and_login()

    response = buy_win720(auth_ctrl, username)
    send_message(1, 1, response=response, notify_target=notify_target)

def lotto_check():
    auth_ctrl, _, notify_target = _setup_and_login()

    response = check_winning_lotto645(auth_ctrl)
    send_message(0, 0, response=response, notify_target=notify_target)

def win720_check():
    auth_ctrl, _, notify_target = _setup_and_login()

    response = check_winning_win720(auth_ctrl)
    send_message(0, 1, response=response, notify_target=notify_target)

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
