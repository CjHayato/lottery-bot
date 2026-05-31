import datetime
import requests
import re

class Notification:
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

        message = f"{win720_round}회 연금복권 구매 완료 :moneybag: 남은잔액 : {body.get('balance', '확인불가')}\n```\n{win720_number_str}```"
        self._send_message(target, message)

    def make_win720_number_message(self, win720_number: str) -> str:
        formatted_numbers = []
        for number in win720_number.split(","):
            formatted_number = f"{number[0]}조 " + " ".join(number[1:])
            formatted_numbers.append(formatted_number)
        return "\n".join(formatted_numbers)

    def send_lotto_winning_message(self, winning: dict, target: dict) -> None:
        assert type(winning) == dict

        balance_str = winning.get('balance', '확인불가')
        try: 
            round = winning["round"]
            money = winning["money"]

            if winning["lotto_details"]:
                max_label_status_length = max(len(f"{line['label']} {line['status']}") for line in winning["lotto_details"])

                formatted_lines = []
                for line in winning["lotto_details"]:
                    line_label_status = f"{line['label']} {line['status']}".ljust(max_label_status_length)
                    line_result = line["result"]
    
                    formatted_nums = []
                    for num in line_result:
                        raw_num = re.search(r'\d+', num).group()
                        formatted_num = f"{int(raw_num):02d}"
                        if '✨' in num:
                            formatted_nums.append(f"[{formatted_num}]")
                        else:
                            formatted_nums.append(f" {formatted_num} ")
    
                    formatted_nums = [f"{num:>6}" for num in formatted_nums]
    
                    formatted_line = f"{line_label_status} " + " ".join(formatted_nums)
                    formatted_lines.append(formatted_line)
    
                formatted_results = "\n".join(formatted_lines)
            else:
                formatted_results = "상세 정보를 불러오지 못했습니다."

            is_winning = winning['money'] != "-" and winning['money'] != "0 원" and winning['money'] != "0"
            
            if is_winning:
                winning_message = f"로또 *{winning['round']}회* - *{winning['money']}* 당첨 되었습니다 🎉 (남은잔액 : {balance_str})"
            else:
                winning_message = f"로또 *{winning['round']}회* - 다음 기회에... 🫠 (남은잔액 : {balance_str})"

            self._send_message(target, f"```ini\n{formatted_results}```\n{winning_message}")
        except KeyError:
            message = f"로또 - 다음 기회에... 🫠 (남은잔액 : {balance_str})"
            self._send_message(target, message)
            return

    def send_win720_winning_message(self, winning: dict, target: dict) -> None:
        assert type(winning) == dict

        balance_str = winning.get('balance', '확인불가')
        try:
            if "win720_details" in winning and winning["win720_details"]:
                max_label_status_length = max(len(f"{line['label']} {line['status']}") for line in winning["win720_details"])
                formatted_lines = []
                for line in winning["win720_details"]:
                    line_label_status = f"{line['label']} {line['status']}".ljust(max_label_status_length)
                    formatted_lines.append(f"{line_label_status} {line['result']}")
                
                formatted_results = "\n".join(formatted_lines)
                message_content = f"```ini\n{formatted_results}```\n"
            else:
                message_content = ""

            is_winning = winning['money'] != "-" and winning['money'] != "0 원" and winning['money'] != "0"

            if is_winning:
                message = f"{message_content}연금복권 *{winning['round']}회* - *{winning['money']}* 당첨 되었습니다 🎉 (남은잔액 : {balance_str})"
            else:
                 message = f"{message_content}연금복권 *{winning['round']}회* - 다음 기회에... 🫠 (남은잔액 : {balance_str})"

            self._send_message(target, message)
        except KeyError:
            message = f"연금복권 - 다음 기회에... 🫠 (남은잔액 : {balance_str})"
            self._send_message(target, message)

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
