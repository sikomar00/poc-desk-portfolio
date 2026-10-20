"""QA 화면용 접속 설정(qa/config.js)을 만든다.

- Supabase CLI(로그인된 상태)로 프로젝트의 공개(anon) 키만 가져온다. 서비스 키·DB 비밀번호는 쓰지 않는다.
- 만들어진 config.js 는 .gitignore 대상이다(공개 키라도 저장소에 올리지 않는다).

실행: python qa/make_config.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT_REF_FILE = ROOT / "supabase" / ".temp" / "project-ref"


def main():
    if not PROJECT_REF_FILE.exists():
        sys.exit("프로젝트가 연결되어 있지 않습니다. 먼저 npx supabase link --project-ref <ref> 를 실행하세요.")
    ref = PROJECT_REF_FILE.read_text(encoding="utf-8").strip()
    r = subprocess.run(["npx", "--yes", "supabase", "projects", "api-keys", "--project-ref", ref, "-o", "json"],
                       capture_output=True, text=True, shell=(sys.platform == "win32"), cwd=ROOT)
    if r.returncode != 0:
        sys.exit("키를 가져오지 못했습니다. npx supabase login 이 되어 있는지 확인하세요.\n" + r.stderr[-300:])
    keys = json.loads(r.stdout)
    anon = next((k["api_key"] for k in keys if k["name"] == "anon"), None)
    if not anon:
        sys.exit("anon 키를 찾지 못했습니다.")
    body = "window.QA_CONFIG = " + json.dumps({"url": f"https://{ref}.supabase.co", "anonKey": anon}, indent=2) + ";\n"
    (ROOT / "qa" / "config.js").write_text(body, encoding="utf-8", newline="\n")
    print("만들었습니다: qa/config.js (공개 키만 포함)")


if __name__ == "__main__":
    main()
