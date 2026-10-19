import datetime
import requests
import re

class Notification:
    _ERROR_REASONS = {
        "timeout": "응답 시간 초과",
        "unauthorized": "로그인 확인 필요",
        "forbidden": "접근 거부",
        "connection_error": "사이트 연결 실패",
        "http_error": "사이트 응답 오류",
        "invalid_json": "응답 형식 오류",
        "invalid_response": "응답 정보 누락 또는 형식 오류",
        "invalid_amount": "금액 정보 오류",
        "detail_error": "상세 정보 조회 실패",
        "authentication_error": "로그인 확인 실패",
        "not_authenticated": "로그인 상태 확인 실패",
        "login_unverified": "로그인 성공 여부 확인 실패",
        "api_error": "사이트 처리 오류",
        "result_pending": "추첨 결과 미확인",
        "incomplete_result": "구매 내역 전체 결과 미확인",
        "check_error": "조회 처리 오류",
        "notification_error": "알림 전송 실패",
    }

    @classmethod
    def safe_error_code(cls, code) -> str:
        return code if isinstance(code, str) and code in cls._ERROR_REASONS else "invalid_response"

    @classmethod
    def error_reason(cls, code) -> str:
        return cls._ERROR_REASONS[cls.safe_error_code(code)]

    @staticmethod
    def winning_status(winning: dict) -> str:
        status = winning.get("status")
        if status == "no_data":
            return status
        if status != "success":
            return "error"
        round_no = winning.get("round")
        if (isinstance(round_no, bool) or not re.fullmatch(r"[0-9]+", str(round_no or "").strip())
                or int(round_no) <= 0):
            return "error"
        money = winning.get("money")
        if isinstance(money, bool) or not isinstance(money, (str, int)):
            return "error"
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*원?", str(money).strip()):
            return "error"
        return "success"

    def send_login_failure_message(self, target: dict) -> None:
        self._send_message(target, "로그인 확인 실패, 당첨 여부 미확인. 사이트 로그인 상태를 확인해 주세요.")

    def _winning_summary(self, label: str, winning: dict) -> str:
        balance = winning.get("balance", "확인불가")
        status = self.winning_status(winning)
        if status == "error":
            reason = self.error_reason(winning.get("error_code"))
            return f"{label} - 당첨 조회 실패 (당첨 여부 미확인) · {reason} (남은잔액 : {balance})"
        if status == "no_data":
            return f"{label} - 조회 기간 내 구매 내역 없음 (남은잔액 : {balance})"
        amount = int(re.sub(r"[,\s원]", "", str(winning["money"])))
        if amount:
            return f"{label} *{winning['round']}회* - *{winning['money']}* 당첨 되었습니다 🎉 (남은잔액 : {balance})"
        return f"{label} *{winning['round']}회* - 다음 기회에... 🫠 (남은잔액 : {balance})"

    def _detail_warning(self, winning: dict) -> str:
        if winning.get("detail_error"):
            code = winning.get("detail_error_code", "detail_error")
            if code == "detail_error":
                return "상세 정보 조회 실패 (번호·등수 미확인)"
            return f"상세 정보 조회 실패 ({self.error_reason(code)})"
        return ""

    def send_lotto_buying_message(self, body: dict, target: dict) -> None:

        result = body.get("result", {})
        if result.get("resultMsg", "FAILURE").upper() != "SUCCESS":  
            message = f"로또 구매 실패 (`{result.get('resultMsg', 'Unknown Error')}`) 남은잔액 : {body.get('balance', '확인불가')}"
            self._send_message(target, message)
            return

        lotto_number_str = self.make_lotto_number_message(result["arrGameChoiceNum"])
        message = f"{result['buyRound']}회 로또 구매 완료 :moneybag: 남은잔액 : {body.get('balance', '확인불가')}\n```{lotto_number_str}```"
        self._send_message(target, message)

    def make_lotto_number_message(self, lotto_number: list) -> str:
        assert type(lotto_number) == list

        # parse list without last number 3
        lotto_number = [x[:-1] for x in lotto_number]
        
        # remove alphabet and | replace white space  from lotto_number
        lotto_number = [x.replace("|", " ") for x in lotto_number]
        
        # lotto_number to string 
        lotto_number = '\n'.join(x for x in lotto_number)
        
        return lotto_number

    def send_win720_buying_message(self, body: dict, target: dict) -> None:
        
        if body.get("resultCode") != '100':  
            message = f"연금복권 구매 실패 (`{body.get('resultMsg', 'Unknown Error')}`) 남은잔액 : {body.get('balance', '확인불가')}"
            self._send_message(target, message)
            return       

        win720_round = body.get("round", "?")
        if win720_round == "?":
            try:
                 win720_round = body.get("saleTicket", "").split("|")[-2]
            except (IndexError, AttributeError, TypeError):
                 win720_round = "?"

        if not body.get("saleTicket"):
            win720_number_str = "번호 정보 없음"
        else:
            win720_number_str = self.make_win720_number_message(body.get("saleTicket"))

        message = f"{win720_round}회 연금복권 구매 완료 :moneybag: 남은잔액 : {body.get('balance', '확인불가')}\n```{win720_number_str}```"
        self._send_message(target, message)

    def make_win720_number_message(self, win720_number: str) -> str:
        formatted_numbers = []
        for number in win720_number.split(","):
            formatted_number = f"{number[0]}조 " + " ".join(number[1:])
            formatted_numbers.append(formatted_number)
        return "\n".join(formatted_numbers)

    def send_lotto_winning_message(self, winning: dict, target: dict) -> None:
        assert type(winning) == dict
        summary = self._winning_summary("로또", winning)
        if self.winning_status(winning) != "success":
            self._send_message(target, summary)
            return

        formatted_lines = []
        details = winning.get("lotto_details") or []
        try:
            max_length = max((len(f"{line['label']} {line['status']}") for line in details), default=0)
            for line in details:
                label = f"{line['label']} {line['status']}".ljust(max_length)
                numbers = []
                for num in line["result"]:
                    raw = re.search(r"\d+", str(num))
                    if raw is None:
                        raise ValueError("Invalid lottery number")
                    number = f"{int(raw.group()):02d}"
                    numbers.append(f"[{number}]" if "✨" in str(num) else f" {number} ")
                formatted_lines.append(f"{label} " + " ".join(f"{number:>6}" for number in numbers))
        except (KeyError, TypeError, ValueError):
            formatted_lines = []
            winning["detail_error"] = True
        detail_warning = self._detail_warning(winning)
        if detail_warning:
            formatted_lines.append(detail_warning)
        details_message = "```ini\n" + "\n".join(formatted_lines) + "```\n" if formatted_lines else ""
        self._send_message(target, details_message + summary)

    def send_win720_winning_message(self, winning: dict, target: dict) -> None:
        assert type(winning) == dict
        summary = self._winning_summary("연금복권", winning)
        if self.winning_status(winning) != "success":
            self._send_message(target, summary)
            return

        formatted_lines = []
        details = winning.get("win720_details") or []
        try:
            max_length = max((len(f"{line['label']} {line['status']}") for line in details), default=0)
            for line in details:
                label = f"{line['label']} {line['status']}".ljust(max_length)
                formatted_lines.append(f"{label} {line['result']}")
        except (KeyError, TypeError):
            formatted_lines = []
            winning["detail_error"] = True
        detail_warning = self._detail_warning(winning)
        if detail_warning:
            formatted_lines.append(detail_warning)
        details_message = "```ini\n" + "\n".join(formatted_lines) + "```\n" if formatted_lines else ""
        self._send_message(target, details_message + summary)

    def _send_message(self, target: dict, message: str) -> None:
        target = target or {"type": "none"}
        target_type = target.get("type")

        if target_type == "telegram":
            formatted_message = self._format_telegram_message(
                message,
                target.get("sender_server", "GitHub Actions lottery-bot"),
            )
            self._send_telegram_message(target["bot_token"], target["chat_id"], formatted_message)
        elif target_type == "slack":
            self._send_slack_webhook(target.get("webhook_url"), message)
        elif target_type == "discord":
            self._send_discord_webhook(target.get("webhook_url"), message)
        else:
            print(f"[Info] Notification target not found. Message: {message}")

    def _format_telegram_message(self, message: str, sender_server: str) -> str:
        received_at = self._current_kst().strftime("%Y.%m.%d %H:%M:%S")
        content = self._clean_telegram_content(message)
        return (
            f"[수신날짜] {received_at}\n"
            f"[발신서버] {sender_server}\n"
            f"[수신내용]\n"
            f"{content}"
        )

    def _current_kst(self) -> datetime.datetime:
        kst = datetime.timezone(datetime.timedelta(hours=9))
        return datetime.datetime.now(kst)

    def _clean_telegram_content(self, message: str) -> str:
        content = message.replace("```ini\n", "")
        content = content.replace("```\n", "\n")
        content = content.replace("```", "")
        content = content.replace(":moneybag:", "")
        content = content.replace("*", "")
        content = content.replace("`", "")
        content = content.replace("🎉", "")
        content = content.replace("🫠", "")
        content = re.sub(r"[ \t]{2,}", " ", content)

        lines = [line.rstrip() for line in content.splitlines()]
        return "\n".join(lines).strip()

    def _send_telegram_message(self, bot_token: str, chat_id: str, message: str) -> None:
        if not bot_token or not chat_id:
            print(f"[Info] Telegram settings not found. Message: {message}")
            return

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        max_len = 4096
        for start in range(0, len(message), max_len):
            payload = {
                "chat_id": chat_id,
                "text": message[start:start + max_len],
                "disable_web_page_preview": True,
            }
            res = requests.post(url, json=payload, timeout=10)
            res.raise_for_status()

    def _send_slack_webhook(self, webhook_url: str, message: str) -> None:
        if not webhook_url:
            print(f"[Info] Slack webhook URL not found. Message: {message}")
            return

        payload = {"text": message}
        res = requests.post(webhook_url, json=payload, timeout=10)
        res.raise_for_status()

    def _send_discord_webhook(self, webhook_url: str, message: str) -> None:
        if not webhook_url:
            print(f"[Info] Webhook URL not found. Message: {message}")
            return
        
        payload = { "content": message }
        res = requests.post(webhook_url, json=payload, timeout=10)
        res.raise_for_status()
