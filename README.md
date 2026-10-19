# 소개 

동행복권 사이트내에 계정에 예치금만 넣어두시면 이후 매주 로또와 연금복권을 구입하고 당첨을 체크하여 알려드려요!  

# 사용법 

![](./.github/images/check.png)

- 레포지토리를 `fork`  합니다. 
- Settings - Secrets - Add a new secret 메뉴로 들어갑니다
- 환경 변수들을 만들어 줍니다 (.env.sample 참조) 
- 매주 로또 및 연금복권 구매 및 당첨 과정을 자동으로 알려드려요 🎉

## GitHub Actions Secrets

아래 값은 GitHub 저장소의 `Settings > Secrets and variables > Actions > New repository secret`에 추가해야 합니다.

- `USERNAME`: 동행복권 아이디
- `PASSWORD`: 동행복권 비밀번호
- `COUNT`: 로또 구매 수량. 예: `5`
- `TELEGRAM_BOT_TOKEN`: 텔레그램 BotFather에서 받은 봇 토큰
- `TELEGRAM_CHAT_ID`: 알림을 받을 텔레그램 채팅 ID
- `TELEGRAM_SENDER_SERVER`: 텔레그램 메시지의 `[발신서버]`에 표시할 이름. 선택값이며 기본값은 `GitHub Actions lottery-bot`

텔레그램 값이 둘 다 있으면 텔레그램으로 알림을 보냅니다. 텔레그램을 쓰지 않는 경우 `SLACK_WEBHOOK_URL` 또는 `DISCORD_WEBHOOK_URL`을 사용할 수 있습니다.

## 로그인 오류 확인 및 검증

RSA 키 조회가 JSON이 아닌 응답이나 잘못된 키를 반환하면 로그인 절차를 최대 5회 시도합니다. 재시도 간격은 2, 4, 8, 16초이며, 모두 실패하면 작업은 실패 상태로 종료됩니다.

로그의 `stage=homepage`와 `ConnectTimeout`은 사이트 연결 단계의 시간 초과를 뜻합니다. `stage=rsa_key`와 `RSA key response`는 RSA 응답 오류를 뜻하며, HTTP 상태·응답 형식·최종 접속 경로·응답 크기로 진단할 수 있습니다. 응답 원문, 키 값, 쿠키와 URL 쿼리는 진단 정보에 포함하지 않습니다. 지속적인 사이트 연결 문제는 재시도만으로 해결되지 않을 수 있습니다.

로그인 요청의 HTTP 성공만으로 인증 성공을 판단하지 않습니다. 공식 페이지의 로그인 상태 표시를 확인하고, 표시가 없으면 보호된 잔액 API로 확인합니다. 당첨 조회에서는 인증 쿠키를 서버가 지정한 도메인과 경로대로 보존하며, 방문 추적용 `WMONID`를 로그인 세션으로 사용하지 않습니다. 구매 시에는 기존 게임 사이트용 쿠키 공유 처리를 유지합니다.

잔액은 현재 공식 사이트와 같이 포인트·현금성/비현금성 예치금의 입금액에서 출금액을 뺀 합계로 계산합니다. 기존 `totalAmt` 응답도 지원하지만, 필수 금액 정보가 없으면 임의로 `0원`으로 표시하지 않습니다. 기준: [공식 로그인 화면](https://www.dhlottery.co.kr/login), [공식 공통 처리 코드](https://www.dhlottery.co.kr/common/cmmUtil.js?v=20261015).

당첨 확인 알림은 정상 조회, 조회 기간 내 구매 내역 없음, 조회 실패를 구분합니다. 인증 실패·응답 오류·연결 시간 초과를 낙첨으로 표시하지 않습니다. 잔액 또는 상세 정보만 실패한 경우에도 알림에 해당 실패를 표시하고, 두 종목 확인을 마친 뒤 Actions를 실패 상태로 종료합니다. `unauthorized`는 인증 확인이 필요함을, `timeout`은 사이트 응답 시간 초과를 뜻합니다.

연금복권은 공식 구매내역 목록의 `gmInfo`(조·번호), `ltWnResult`(당첨·낙첨), `ltWnAmt`를 검증해 상세 알림을 만듭니다. 조회된 목록이 전체 내역임을 확인한 경우에만 같은 회차의 각 구매 건을 표시하고 당첨금을 합산합니다. 낙첨은 사이트의 명시적인 결과로 판단하며, 등수 누락만으로 낙첨을 추정하지 않습니다. 추첨 결과나 구매 내역 전체를 확인할 수 없으면 알림에도 당첨 여부 미확인으로 표시합니다. 목록이 불완전하거나 해당 정보가 없으면 기존 상세 조회를 진단용으로 유지하며, 실패 로그에는 응답 값 대신 알려진 필드의 자료형과 실패 단계만 남깁니다.

아래 테스트는 HTTP 요청을 모의 처리하므로 실제 로그인, 구매 또는 알림 전송을 수행하지 않습니다.

```sh
python3 -m unittest discover -s tests -v
```

# Reference 
- https://github.com/roeniss/dhlottery-api
