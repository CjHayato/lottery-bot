import copy
import datetime
import requests
import json
import base64
import binascii
import re
import time
from urllib.parse import urlsplit
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
from HttpClient import HttpClientSingleton

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class RSAKeyResponseError(RuntimeError):
    """The RSA endpoint returned an unusable response before login."""


class AuthController:
    _REQ_HEADERS = {
        "User-Agent": USER_AGENT,
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "Upgrade-Insecure-Requests": "1",
        "Origin": "https://www.dhlottery.co.kr",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Referer": "https://www.dhlottery.co.kr/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Accept-Language": "ko,en-US;q=0.9,en;q=0.8,ko-KR;q=0.7",
    }

    _AUTH_CRED = ""

    def __init__(self):
        self.http_client = HttpClientSingleton.get_instance()

    def login(self, user_id: str, password: str):
        assert isinstance(user_id, str)
        assert isinstance(password, str)

        max_retries = 5
        for attempt in range(max_retries):
            stage = "homepage"
            try:
                # 1. Warm-up
                self.http_client.get("https://www.dhlottery.co.kr/")
                stage = "login_page"
                self.http_client.get("https://www.dhlottery.co.kr/user.do?method=login", headers=self._REQ_HEADERS)
                
                # 2. RSA Key Fetch
                stage = "rsa_key"
                modulus, exponent = self._get_rsa_key()

                # 3. Encrypt
                enc_user_id = self._rsa_encrypt(user_id, modulus, exponent)
                enc_password = self._rsa_encrypt(password, modulus, exponent)

                # 4. Prepare Login Request
                headers = copy.deepcopy(self._REQ_HEADERS)
                headers.update({
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://www.dhlottery.co.kr",
                    "Referer": "https://www.dhlottery.co.kr/user.do?method=login"
                })
                
                data = {
                    "userId": enc_user_id,
                    "userPswdEncn": enc_password, 
                    "inpUserId": user_id
                }

                # 5. Execute Login
                stage = "login_request"
                self._try_login(headers, data)
                return # Success
                
            except (requests.RequestException, RSAKeyResponseError) as e:
                # Transport exception messages can contain request URLs or credentials.
                detail = str(e) if isinstance(e, RSAKeyResponseError) else type(e).__name__
                if attempt == max_retries - 1:
                     print(f"[Error] Login sequence failed after {max_retries} attempts "
                           f"(stage={stage}): {detail}", flush=True)
                     raise
                delay = 2 ** (attempt + 1)
                print(f"[Retry] Login failed ({attempt+1}/{max_retries}, stage={stage}): "
                      f"{detail}. Retrying in {delay}s...", flush=True)
                time.sleep(delay)
        
    def add_auth_cred_to_headers(self, headers: dict) -> str:
        assert isinstance(headers, dict)

        copied_headers = copy.deepcopy(headers)
        return copied_headers

    def _get_default_auth_cred(self):
        res = self.http_client.get(
            "https://www.dhlottery.co.kr/common.do?method=main"
        )
        return self._get_j_session_id_from_response(res)

    def _get_rsa_key(self):
        headers = copy.deepcopy(self._REQ_HEADERS)
        headers.update({
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.dhlottery.co.kr/user.do?method=login"
        })
        headers.pop("Upgrade-Insecure-Requests", None)

        res = self.http_client.get(
            "https://www.dhlottery.co.kr/login/selectRsaModulus.do",
            headers=headers
        )
        
        try:
            data = res.json()
        except ValueError as e:
            raise RSAKeyResponseError(
                f"RSA key response is not valid JSON. {self._rsa_response_summary(res)}"
            ) from e

        if not isinstance(data, dict):
            raise RSAKeyResponseError(
                f"RSA key response must be a JSON object. {self._rsa_response_summary(res)}"
            )

        key_data = data.get("data")
        if not isinstance(key_data, dict) or "rsaModulus" not in key_data:
            key_data = data

        modulus = key_data.get("rsaModulus")
        exponent = key_data.get("publicExponent")
        if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]+", value)
                   for value in (modulus, exponent)):
            raise RSAKeyResponseError(
                f"RSA key response has missing or invalid key fields. {self._rsa_response_summary(res)}"
            )

        try:
            public_exponent = int(exponent, 16)
            if public_exponent < 3 or public_exponent % 2 == 0:
                raise ValueError("RSA public exponent must be odd and greater than one")
            RSA.construct((int(modulus, 16), public_exponent))
        except ValueError as e:
            raise RSAKeyResponseError(
                f"RSA key response has invalid public key parameters. {self._rsa_response_summary(res)}"
            ) from e

        return modulus, exponent

    @staticmethod
    def _rsa_response_summary(res: requests.Response) -> str:
        # Never include the response body, key values, cookies, or URL query.
        final_url = urlsplit(res.url or "")
        content_type = res.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", content_type):
            content_type = "unknown"
        body = res.text.lstrip()
        body_kind = "empty" if not body else "html" if body.startswith("<") else "other"
        return json.dumps({
            "status": res.status_code,
            "content_type": content_type,
            "final_host": final_url.hostname,
            "final_path": final_url.path.split(";", 1)[0],
            "response_bytes": len(res.content),
            "body_kind": body_kind,
        }, ensure_ascii=True)

    def _rsa_encrypt(self, text, modulus, exponent):
        key_spec = RSA.construct((int(modulus, 16), int(exponent, 16)))
        cipher = PKCS1_v1_5.new(key_spec)
        ciphertext = cipher.encrypt(text.encode('utf-8'))
        return binascii.hexlify(ciphertext).decode('utf-8')

    def _get_j_session_id_from_response(self, res: requests.Response):
        assert isinstance(res, requests.Response)

        for cookie in res.cookies:
            if cookie.name == "JSESSIONID":
                return cookie.value
        
        return self.get_current_session_id()

    def _generate_req_headers(self):
        return copy.deepcopy(self._REQ_HEADERS)

    def _try_login(self, headers: dict, data: dict):
        assert isinstance(headers, dict)
        assert isinstance(data, dict)
        
        res = self.http_client.post(
            "https://www.dhlottery.co.kr/login/securityLoginCheck.do",
            headers=headers,
            data=data,
        )
        
        new_jsessionid = self._get_j_session_id_from_response(res)
        if new_jsessionid:
             self._update_auth_cred(new_jsessionid)

        try:
             self.http_client.get("https://www.dhlottery.co.kr/main", headers=self._REQ_HEADERS)
        except Exception as e:
             print(f"[Warning] Failed to check main page after login: {e}")
             
        return res

    def _update_auth_cred(self, j_session_id: str) -> None:
        assert isinstance(j_session_id, str)
        self._AUTH_CRED = j_session_id
        
        self.http_client.session.cookies.set("JSESSIONID", j_session_id, domain=".dhlottery.co.kr")

        wmonid = None
        for cookie in self.http_client.session.cookies:
             if cookie.name == "WMONID":
                 wmonid = cookie.value
                 break
        
        if wmonid:
             self.http_client.session.cookies.set("WMONID", wmonid, domain=".dhlottery.co.kr")

    def get_current_session_id(self) -> str:
        for cookie in self.http_client.session.cookies:
            if cookie.name in ["JSESSIONID", "DHJSESSIONID", "WMONID"]:
                return cookie.value
        
        if self._AUTH_CRED:
            return self._AUTH_CRED

        return ""
            
    def get_user_balance(self) -> str:
        last_error = None
        
        for attempt in range(3):
            try:
                 try:
                     self.http_client.get("https://www.dhlottery.co.kr/mypage/home")
                 except requests.RequestException:
                     pass

                 timestamp = int(datetime.datetime.now().timestamp() * 1000)
                 url = f"https://www.dhlottery.co.kr/mypage/selectUserMndp.do?_={timestamp}"
                 
                 headers = copy.deepcopy(self._REQ_HEADERS)
                 headers.update({
                    "Referer": "https://www.dhlottery.co.kr/mypage/home",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/json;charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "requestMenuUri": "/mypage/home",
                    "AJAX": "true",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Dest": "empty"
                 })
                 
                 res = self.http_client.get(url, headers=headers)
                 
                 txt = res.text.strip()
                 if txt.startswith("<"):
                      return "확인 불가 (로그인/설정)"

                 data = json.loads(txt)
                 
                 if 'data' in data and isinstance(data['data'], dict):
                     data = data['data']

                 if 'userMndp' in data:
                     data = data['userMndp']
                     
                 if 'totalAmt' in data:
                     val = str(data['totalAmt']).replace(',', '')
                     return f"{int(val):,}원"
                 
                 return "0원"

            except Exception as e:
                 last_error = e
                 time.sleep(1)
        
        # If all retries failed
        print(f"[Error] 잔액 조회에 실패했습니다: {last_error}")
        return f"정보 로드 실패 (로그 확인)"
