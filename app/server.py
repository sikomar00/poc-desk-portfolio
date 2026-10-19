"""PoC Desk 로컬 웹 서버 (표준 라이브러리만 사용, 설치 불필요).

실행: python app/server.py            → http://127.0.0.1:8765 이 자동으로 열린다
      python app/server.py --port 9000 --no-browser

보안: 내 PC(127.0.0.1)에서만 접속된다. 다른 웹사이트가 이 서버로 요청을 보내는 것을 막기 위해
Host·Origin 검사, JSON 요청만 허용, 사용자 지정 헤더(X-PocDesk) 확인을 한다. API 키는 화면으로 나가지 않는다.
"""
import argparse
import json
import mimetypes
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 100_000
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "PocDesk/1.0"

    def log_message(self, fmt, *args):  # 조용히: 오류만 표준 오류로
        if args and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---- 보조
    def _allowed_hosts(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, code, body, ctype, extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _error(self, code, message):
        self._json({"error": message}, code)

    # ---- GET
    def do_GET(self):
        if self.headers.get("Host", "") not in self._allowed_hosts():
            return self._error(403, "허용되지 않은 접속 주소입니다.")
        url = urlparse(self.path)
        path = url.path
        if path.startswith("/api/"):
            return self._api_get(path, parse_qs(url.query))
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self._error(404, "찾을 수 없습니다.")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _api_get(self, path, q):
        try:
            if path == "/api/status":
                return self._json({"llm_key": engine.llm_key_status(), "default_model": engine.DEFAULT_MODEL,
                                   "data_months": engine.VALID_MONTHS})
            if path == "/api/dictionary":
                return self._json({"metrics": engine.dictionary()})
            if path == "/api/history":
                return self._json({"runs": engine.history(50)})
            if path == "/api/run":
                return self._json(engine.get_run((q.get("id") or [""])[0]))
        except ValueError as e:
            return self._error(404, str(e))
        self._error(404, "찾을 수 없습니다.")

    # ---- POST
    def do_POST(self):
        if self.headers.get("Host", "") not in self._allowed_hosts():
            return self._error(403, "허용되지 않은 접속 주소입니다.")
        origin = self.headers.get("Origin")
        if origin and origin.replace("http://", "") not in self._allowed_hosts():
            return self._error(403, "허용되지 않은 출처입니다.")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error(415, "JSON 요청만 받습니다.")
        if self.headers.get("X-PocDesk") != "1":
            return self._error(403, "잘못된 요청입니다.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "잘못된 요청입니다.")
        if length > MAX_BODY:
            return self._error(413, "요청이 너무 큽니다.")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._error(400, "요청 형식이 올바르지 않습니다.")
        path = urlparse(self.path).path
        try:
            if path == "/api/intake":
                return self._json(engine.intake(body.get("question")))
            if path == "/api/definition":
                aliases = body.get("aliases") or []
                if isinstance(aliases, str):
                    aliases = [a for a in aliases.replace("，", ",").split(",")]
                return self._json(engine.save_definition(body.get("name"), aliases, body.get("formula"),
                                                         body.get("include_exclude"), body.get("period_basis"),
                                                         body.get("unit")))
            if path == "/api/run":
                return self._json(engine.run_query(str(body.get("question") or "")[:300], body.get("metric_key"),
                                                   list(body.get("months") or []), body.get("mode") or "reference",
                                                   body.get("model")))
            if path == "/api/report":
                return self._json(engine.make_report(str(body.get("run_id") or "")))
        except ValueError as e:
            return self._error(400, str(e))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"내부 오류: {type(e).__name__}: {e}\n")
            return self._error(500, "처리 중 오류가 발생했습니다.")
        self._error(404, "찾을 수 없습니다.")


def make_server(port=8765):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    server = make_server(a.port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"PoC Desk 실행 중: {url}  (끄려면 Ctrl+C)")
    print("AI 모드:", "API 키 설정됨" if engine.llm_key_status() else "API 키 없음 (검증된 계산식 모드만 사용 가능)")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
