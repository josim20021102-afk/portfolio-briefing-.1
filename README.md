# Portfolio Kakao Daily Briefing

보유 종목 `삼성전자우`, `알파벳A`, `JEPQ`, `NVDL`, `TQQQ`의 최근 종가와 오늘자 국내/해외 뉴스, 시장/거시 전망을 모아 카카오톡 “나에게 보내기”로 전송하는 스크립트입니다.

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

현재 Codex 번들 Python으로 실행하려면 아래 경로를 사용할 수 있습니다.

```powershell
C:\Users\hcl20\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pip install -r requirements.txt
```

## 필수 카카오 설정

Kakao Developers 문서 기준, “나에게 메시지 보내기”는 `https://kapi.kakao.com/v2/api/talk/memo/default/send` 엔드포인트에 액세스 토큰으로 요청합니다.

1. [Kakao Developers](https://developers.kakao.com/)에서 애플리케이션을 생성합니다.
2. 앱 키에서 `REST API 키`를 확인해 `.env`의 `KAKAO_REST_API_KEY`에 입력합니다.
3. 카카오 로그인 Redirect URI를 등록합니다. 로컬 테스트용이면 예: `http://localhost:8080/callback`
4. 동의 항목에서 카카오톡 메시지 전송 권한, 즉 `talk_message` scope를 활성화합니다.
5. OAuth 인증 코드로 refresh token을 발급받아 `.env`의 `KAKAO_REFRESH_TOKEN`에 입력합니다.

Kakao Developers의 `카카오 로그인 > 보안`에서 Client Secret을 활성화했다면 `.env`의 `KAKAO_CLIENT_SECRET`에도 값을 입력해야 합니다.

refresh token 발급은 보조 스크립트로 받을 수 있습니다.

```powershell
python kakao_token_setup.py
```

Codex 번들 Python을 쓰는 경우:

```powershell
C:\Users\hcl20\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe kakao_token_setup.py
```

## 선택 네이버 뉴스 API

국내 뉴스 정확도를 높이고 싶다면 [NAVER Developers](https://developers.naver.com/)에서 애플리케이션을 만든 뒤 “검색” API 사용 설정을 하고, 발급된 값을 `.env`에 넣습니다.

```text
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
```

없어도 Google News RSS로 대체 수집합니다.

## 실행

```powershell
.\.venv\Scripts\Activate.ps1
python portfolio_briefing.py
```

카톡 전송 없이 출력만 확인하려면:

```powershell
$env:KAKAO_SEND='false'
python portfolio_briefing.py
```

카카오톡은 기본 텍스트 템플릿 제한 때문에 200자 요약 1개만 보내고, 전문은 `reports/` 폴더에 HTML로 저장합니다. 버튼을 전문 페이지로 연결하려면 공개 URL을 `.env`에 설정하세요.

```text
REPORT_BASE_URL=https://your-id.github.io/your-repo
```

카카오 Developers의 Product Link에도 같은 웹 도메인을 등록해야 버튼 링크가 정상 동작합니다. 특정 URL 하나로 고정하고 싶으면 `KAKAO_DETAIL_URL`을 사용할 수 있습니다.

## 컴퓨터가 꺼져도 매일 보내기

무료로 가장 쉬운 방법은 GitHub Actions입니다. 이 저장소를 GitHub에 올리고, repository secrets에 아래 값을 등록합니다.

```text
KAKAO_REST_API_KEY
KAKAO_CLIENT_SECRET
KAKAO_REFRESH_TOKEN
NAVER_CLIENT_ID
NAVER_CLIENT_SECRET
GEMINI_API_KEY
```

GitHub Pages는 `Settings > Pages > Build and deployment > Source`를 `GitHub Actions`로 설정합니다.

워크플로우 파일은 `.github/workflows/daily-portfolio-briefing.yml`입니다. 매일 오전 7시 KST에 실행되도록 `0 22 * * *` UTC cron으로 설정되어 있고, `Actions` 탭에서 수동 실행도 가능합니다.

## 참고

- 삼성전자우 가격은 `pykrx`로 가져옵니다.
- 알파벳A, JEPQ, NVDL, TQQQ 가격은 `yfinance`로 가져옵니다.
- 미국 종목은 한국 시간 기준 “현재 시점에서 가장 최근에 완료된 정규장 종가”를 사용합니다.
- `OPENAI_API_KEY`를 넣으면 영어 뉴스 제목/요약을 한국어로 번역하고 압축합니다. 없으면 영어 원문 제목을 그대로 포함합니다.
- `GEMINI_API_KEY`를 넣으면 `OPENAI_API_KEY`가 없을 때 Gemini로 영어 뉴스를 한국어 번역/요약합니다.
