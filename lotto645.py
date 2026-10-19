import datetime
import json
import requests
import time

from datetime import timedelta
from enum import Enum

from bs4 import BeautifulSoup as BS

import auth
import common
import logging
from HttpClient import HttpClientSingleton

logger = logging.getLogger(__name__)

class Lotto645Mode(Enum):
    AUTO = 1
    MANUAL = 2
    BUY = 10 
    CHECK = 20

class Lotto645:

    _REQ_HEADERS = {
        "User-Agent": auth.USER_AGENT,
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
        "Origin": "https://ol.dhlottery.co.kr",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Referer": "https://ol.dhlottery.co.kr/olotto/game/game645.do",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Accept-Language": "ko,en-US;q=0.9,en;q=0.8,ko-KR;q=0.7",
    }

    def __init__(self):
        self.http_client = HttpClientSingleton.get_instance()

    def buy_lotto645(
        self, 
        auth_ctrl: auth.AuthController, 
        cnt: int, 
        mode: Lotto645Mode
    ) -> dict:
        assert isinstance(auth_ctrl, auth.AuthController)
        assert isinstance(cnt, int) and 1 <= cnt <= 5
        assert isinstance(mode, Lotto645Mode)

        headers = self._generate_req_headers(auth_ctrl)
        
        requirements = self._getRequirements(headers)
        
        data = (
            self._generate_body_for_auto_mode(cnt, requirements)
            if mode == Lotto645Mode.AUTO
            else self._generate_body_for_manual(cnt)
        )

        body = self._try_buying(headers, data)

        self._show_result(body)
        return body

    def _generate_req_headers(self, auth_ctrl: auth.AuthController) -> dict:
        assert isinstance(auth_ctrl, auth.AuthController)
        return auth_ctrl.add_auth_cred_to_headers(self._REQ_HEADERS)

    def _generate_body_for_auto_mode(self, cnt: int, requirements: list) -> dict:
        assert isinstance(cnt, int) and 1 <= cnt <= 5

        return {
            "round": requirements[3],
            "direct": requirements[0], 
            "nBuyAmount": str(1000 * cnt),
            "param": json.dumps(
                [
                    {"genType": "0", "arrGameChoiceNum": None, "alpabet": slot}
                    for slot in common.SLOTS[:cnt]
                ]
            ),
            'ROUND_DRAW_DATE' : requirements[1],
            'WAMT_PAY_TLMT_END_DT' : requirements[2],
            "gameCnt": cnt,
            "saleMdaDcd": "10"
        }

    def _generate_body_for_manual(self, cnt: int) -> dict:
        assert isinstance(cnt, int) and 1 <= cnt <= 5
        raise NotImplementedError()

    def _getRequirements(self, headers: dict) -> list:
        headers["Referer"] = "https://ol.dhlottery.co.kr/olotto/game/game645.do"
        headers["Origin"] = "https://ol.dhlottery.co.kr"
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Sec-Fetch-Site"] = "same-origin"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Dest"] = "empty"

        # Retry logic for egovUserReadySocket.json
        max_retries = 5
        for attempt in range(max_retries):
            try:
                res = self.http_client.post(
                    url="https://ol.dhlottery.co.kr/olotto/game/egovUserReadySocket.json", 
                    headers=headers
                )
                res.raise_for_status() # Check for HTTP errors
                break # Success
            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    logger.warning(f"[Retry] ReadySocket connection failed ({attempt+1}/{max_retries}): {e}. Retrying in 2s...")
                    time.sleep(2)
                else:
                    logger.error(f"[Error] ReadySocket connection failed after {max_retries} attempts: {e}")
                    raise
        
        direct = json.loads(res.text)["ready_ip"]
        
        html_headers = self._REQ_HEADERS.copy()
        html_headers.pop("Origin", None)
        html_headers.pop("Content-Type", None)
        html_headers["Referer"] = "https://www.dhlottery.co.kr/common.do?method=main"
        
        if headers.get("Cookie"):
            html_headers["Cookie"] = headers.get("Cookie")
            
        res = self.http_client.get(
            url="https://ol.dhlottery.co.kr/olotto/game/game645.do", 
            headers=html_headers
        )
        html = res.text
        soup = BS(html, "html5lib")
        
        try:
            draw_date_el = soup.find("input", id="ROUND_DRAW_DATE")
            tlmt_date_el = soup.find("input", id="WAMT_PAY_TLMT_END_DT")
            
            if draw_date_el and tlmt_date_el:
                draw_date = draw_date_el.get('value')
                tlmt_date = tlmt_date_el.get('value')
            else:
                raise ValueError("Date inputs not found")
        except (ValueError, AttributeError, TypeError) as e:
            logger.error(f"[Error] Date extraction failed: {e}")
            today = datetime.datetime.today()
            days_ahead = (5 - today.weekday()) % 7
            next_saturday = today + datetime.timedelta(days=days_ahead)
            draw_date = next_saturday.strftime("%Y-%m-%d")
            
            limit_date = next_saturday + datetime.timedelta(days=366)
            tlmt_date = limit_date.strftime("%Y-%m-%d")

        
        cur_round_input = soup.find("input", id="curRound")
        if cur_round_input:
            current_round = cur_round_input.get('value')
        else:
            current_round = self._get_round()

        return [direct, draw_date, tlmt_date, current_round]

    def _get_round(self) -> str:
        try:
            res = self.http_client.get(
                "https://www.dhlottery.co.kr/common.do?method=main",
                headers=self._REQ_HEADERS
            )
            html = res.text
            soup = BS(html, "html5lib")
            found = soup.find("strong", id="lottoDrwNo")
            if found:
                last_drawn_round = int(found.text)
                return str(last_drawn_round + 1)
            else:
                 raise ValueError("lottoDrwNo not found")
        except Exception as e:
            base_date = datetime.datetime(2024, 12, 28)
            base_round = 1152
            
            today = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
            
            days_ahead = (5 - today.weekday()) % 7
            next_saturday = today + datetime.timedelta(days=days_ahead)
            
            weeks = (next_saturday - base_date).days // 7
            return str(base_round + weeks)




        
    def _try_buying(self, headers: dict, data: dict) -> dict:
        assert isinstance(headers, dict)
        assert isinstance(data, dict)

        headers["Content-Type"]  = "application/x-www-form-urlencoded; charset=UTF-8"

        max_retries = 5
        for attempt in range(max_retries):
            try:
                res = self.http_client.post(
                    "https://ol.dhlottery.co.kr/olotto/game/execBuy.do",
                    headers=headers,
                    data=data,
                )
                res.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    logger.warning(f"[Retry] execBuy connection failed ({attempt+1}/{max_retries}): {e}. Retrying in 2s...")
                    time.sleep(2)
                else:
                    logger.error(f"[Error] execBuy connection failed after {max_retries} attempts: {e}")
                    raise
        if res.encoding == 'ISO-8859-1':
             res.encoding = 'euc-kr'
        
        try:
             return json.loads(res.text)
        except UnicodeDecodeError:
             res.encoding = 'euc-kr' 
             return json.loads(res.text)

    def check_winning(self, auth_ctrl: auth.AuthController) -> dict:
        assert isinstance(auth_ctrl, auth.AuthController)

        headers = self._REQ_HEADERS.copy()
        headers.update({
            "Referer": "https://www.dhlottery.co.kr/mypage/mylotteryledger",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "AJAX": "true",
            "requestMenuUri": "/mypage/mylotteryledger",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })
        headers.pop("Content-Type", None)
        headers.pop("Origin", None)
        headers.pop("Upgrade-Insecure-Requests", None)
        headers.pop("Sec-Fetch-User", None)

        parameters = common.get_search_date_range()
        params = {
            "srchStrDt": parameters["searchStartDate"],
            "srchEndDt": parameters["searchEndDate"],
            "ltGdsCd": "LO40",
            "pageNum": 1,
            "recordCountPerPage": 10,
        }

        try:
            payload = common.get_json_response(
                self.http_client,
                "https://www.dhlottery.co.kr/mypage/selectMyLotteryledger.do",
                params=params, headers=headers,
            )
            data = payload.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise common.LotteryQueryError("invalid_response")
            if not data["list"]:
                return {"status": "no_data"}

            item = data["list"][0]
            if not isinstance(item, dict):
                raise common.LotteryQueryError("invalid_response")
            round_no = item.get("ltEpsdView")
            if not isinstance(round_no, (str, int)) or isinstance(round_no, bool):
                raise common.LotteryQueryError("invalid_response")
            round_no = str(round_no).replace("회", "").strip()
            if not round_no.isdigit():
                raise common.LotteryQueryError("invalid_response")
            amount = common.parse_amount(item.get("ltWnAmt"))
            result_data = {
                "status": "success",
                "round": round_no,
                "money": f"{amount:,} 원",
                "purchased_date": item.get("eltOrdrDt", "-"),
                "winning_date": item.get("epsdRflDt", "-"),
                "lotto_details": [],
            }
        except common.LotteryQueryError as error:
            logger.error("[Error] Lotto check failed (code=%s)", error.code)
            return {"status": "error", "error_code": error.code}

        detail_params = {
            "ltGdsCd": item.get("ltGdsCd"),
            "ltEpsd": item.get("ltEpsd"),
            "barcd": item.get("gmInfo"),
            "ntslOrdrNo": item.get("ntslOrdrNo"),
            "srchStrDt": params["srchStrDt"],
            "srchEndDt": params["srchEndDt"],
        }
        try:
            detail_data = common.get_json_response(
                self.http_client,
                "https://www.dhlottery.co.kr/mypage/lotto645TicketDetail.do",
                params=detail_params, headers=headers,
            )
            detail_data = detail_data.get("data", detail_data)
            if not isinstance(detail_data, dict):
                raise common.LotteryQueryError("detail_error")
            ticket = detail_data.get("ticket")
            if ticket is None and isinstance(detail_data.get("data"), dict):
                ticket = detail_data["data"].get("ticket")
            if not isinstance(ticket, dict):
                raise common.LotteryQueryError("detail_error")
            game_dtl = ticket.get("game_dtl")
            win_num = ticket.get("win_num")
            if not isinstance(game_dtl, list) or not game_dtl or not isinstance(win_num, list):
                raise common.LotteryQueryError("detail_error")

            lotto_details = []
            for i, game in enumerate(game_dtl):
                if not isinstance(game, dict) or not isinstance(game.get("num"), list):
                    raise common.LotteryQueryError("detail_error")
                rank = game.get("rank")
                if isinstance(rank, bool) or not isinstance(rank, (str, int)) or not str(rank).isdigit():
                    raise common.LotteryQueryError("detail_error")
                status = "낙첨" if int(rank) == 0 else f"{int(rank)}등"
                formatted_nums = [f"✨{num}" if num in win_num else str(num) for num in game["num"]]
                lotto_details.append({
                    "label": common.SLOTS[i] if i < len(common.SLOTS) else "?",
                    "status": status,
                    "result": formatted_nums,
                })
            result_data["lotto_details"] = lotto_details
        except common.LotteryQueryError:
            logger.error("[Error] Lotto detail failed (code=detail_error)")
            result_data.update({"detail_error": True, "error_code": "detail_error"})

        return result_data
    

    def _show_result(self, body: dict) -> None:
        assert isinstance(body, dict)

        if body.get("loginYn") != "Y":
            return

        result = body.get("result", {})
        if result.get("resultMsg", "FAILURE").upper() != "SUCCESS":    
            return
