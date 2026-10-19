import datetime
import logging
import re
import time
from datetime import timedelta

import requests


logger = logging.getLogger(__name__)


class LotteryQueryError(RuntimeError):
    """A read-only request failed; expose only a fixed diagnostic code."""

    _CODES = {
        "timeout", "unauthorized", "forbidden", "connection_error", "http_error",
        "invalid_json", "invalid_response", "invalid_amount", "detail_error", "api_error",
    }

    def __init__(self, code: str):
        self.code = code if code in self._CODES else "invalid_response"
        super().__init__(self.code)


def parse_amount(value) -> int:
    """Accept an explicit nonnegative whole amount, never an implicit zero."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise LotteryQueryError("invalid_amount")
    text = str(value).strip()
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", text):
        raise LotteryQueryError("invalid_amount")
    try:
        return int(text.replace(",", ""))
    except ValueError:
        raise LotteryQueryError("invalid_amount") from None


def get_json_response(http_client, url, headers=None, params=None) -> dict:
    """Read JSON with bounded retries without logging account/request data."""
    for attempt in range(3):
        retryable = False
        try:
            response = http_client.get(url, headers=headers, params=params)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                code = "invalid_json"
                retryable = True
            else:
                if isinstance(data, dict) and data.get("resultCode"):
                    code = "api_error"
                elif isinstance(data, dict):
                    return data
                else:
                    code = "invalid_response"
        except requests.Timeout:
            code = "timeout"
            retryable = True
        except requests.ConnectionError:
            code = "connection_error"
            retryable = True
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else None
            if status == 401:
                code = "unauthorized"
            elif status == 403:
                code = "forbidden"
            else:
                code = "http_error"
                retryable = status is not None and 500 <= status < 600
        except requests.RequestException:
            code = "http_error"

        if retryable and attempt < 2:
            time.sleep(2 ** (attempt + 1))
            continue

        logger.error("[Error] Query failed (code=%s)", code)
        raise LotteryQueryError(code) from None

def get_search_date_range() -> dict:
    today = datetime.datetime.today()
    today_str = today.strftime("%Y%m%d")
    weekago = today - timedelta(days=7)
    weekago_str = weekago.strftime("%Y%m%d")
    return {
        "searchStartDate": weekago_str,
        "searchEndDate": today_str
    }

SLOTS = ["A", "B", "C", "D", "E"]
