from __future__ import annotations

import os
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv


REDIRECT_URI = "http://localhost:8080/callback"


class CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        CallbackHandler.code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("카카오 인증이 완료되었습니다. 이 창을 닫아도 됩니다.".encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    load_dotenv()
    rest_key = os.getenv("KAKAO_REST_API_KEY", "").strip()
    client_secret = os.getenv("KAKAO_CLIENT_SECRET", "").strip()
    if not rest_key:
        raise RuntimeError(".env에 KAKAO_REST_API_KEY를 먼저 입력하세요.")

    authorize_url = (
        "https://kauth.kakao.com/oauth/authorize?"
        + urllib.parse.urlencode(
            {
                "client_id": rest_key,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": "talk_message",
            }
        )
    )

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("아래 URL을 브라우저에서 열어 카카오 동의를 완료하세요.")
    print(authorize_url)
    try:
        webbrowser.open(authorize_url)
    except Exception:
        pass

    thread.join(timeout=180)
    server.server_close()
    if not CallbackHandler.code:
        raise RuntimeError("3분 안에 인증 코드가 도착하지 않았습니다.")

    payload = {
        "grant_type": "authorization_code",
        "client_id": rest_key,
        "redirect_uri": REDIRECT_URI,
        "code": CallbackHandler.code,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    response = requests.post("https://kauth.kakao.com/oauth/token", data=payload, timeout=20)
    response.raise_for_status()
    data = response.json()
    print("\n.env에 아래 값을 입력하세요.")
    print(f"KAKAO_REFRESH_TOKEN={data['refresh_token']}")


if __name__ == "__main__":
    main()
