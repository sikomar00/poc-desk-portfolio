"""Vercel 진입점: 같은 서버 코드(app/server.py)를 서버리스 함수로 돌린다.

공개 데모 설정(비밀이 아닌 기본값). 접속 정보·비밀번호는 Vercel 환경변수로만 받는다(저장소에 없다).
  POC_DESK_BACKEND=supabase  클라우드 DB(합성 데이터)를 쓴다
  POC_DESK_PUBLIC=1          공개 모드: 같은 출처만 허용, 비로그인 실행은 DB에 기록 안 함, 정의 저장 끔, 속도 제한
  POC_DESK_DB_PERSIST=0      요청마다 DB 연결을 닫는다(서버리스에서 연결이 남아 쌓이지 않게)
쓰기 가능한 곳은 /tmp 뿐이라 리포트 파일 위치도 /tmp 로 둔다(재시작하면 사라짐).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("POC_DESK_BACKEND", "supabase")
os.environ.setdefault("POC_DESK_PUBLIC", "1")
os.environ.setdefault("POC_DESK_DB_PERSIST", "0")
os.environ.setdefault("POC_DESK_DATA_DIR", "/tmp/pocdesk/data")
os.environ.setdefault("POC_DESK_REPORT_DIR", "/tmp/pocdesk/reports")

ROOT = Path(__file__).resolve().parent.parent
for sub in ("app", "experiment"):
    sys.path.insert(0, str(ROOT / sub))

import server  # noqa: E402


class handler(server.Handler):  # Vercel 은 BaseHTTPRequestHandler 를 상속한 `handler` 클래스를 찾는다
    pass
