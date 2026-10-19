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

아래 테스트는 HTTP 요청을 모의 처리하므로 실제 로그인, 구매 또는 알림 전송을 수행하지 않습니다.

```sh
python3 -m unittest discover -s tests -v
```

# Reference 
- https://github.com/roeniss/dhlottery-api
