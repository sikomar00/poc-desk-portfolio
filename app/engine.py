"""realDefinition 엔진: 질문 해석, KPI 사전, 실행, 대사, 리포트.

HTTP와 분리해 두어 단독으로 테스트할 수 있다. 설계는 docs/03-prd.md 의 F1~F7, F9 를 따른다.

실행 방식 두 가지
  reference  사람이 검토한 지표 계산식(experiment/gold.py)을 그대로 실행한다. (시맨틱 레이어에 가까운 방식)
  ai         LLM이 SQL을 만든다. 테이블 구조와 KPI 사전만 보내고 거래 행은 보내지 않는다. 결과는 기준 계산식·원장과 대조한다.

중요: 정답 출처가 없는 지표(사용자가 새로 정의한 지표)는 '검증됨'을 붙이지 않고 '검증 불가'로 표시한다 (결정 D-025).
"""
import collections
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(ROOT / "experiment"))

import run_experiment as R  # noqa: E402  (안전 검사, 읽기 전용 실행, 프롬프트 구성을 실험과 똑같이 쓴다)
from gold import gold_sql as _sqlite_gold_sql  # noqa: E402
import db  # noqa: E402  (Supabase 연결 계층. POC_DESK_BACKEND=supabase 일 때만 쓰고, 기본은 SQLite)

DATA_DIR = Path(os.environ.get("POC_DESK_DATA_DIR", APP_DIR / "data"))
REPORT_DIR = Path(os.environ.get("POC_DESK_REPORT_DIR", APP_DIR / "reports"))
VALID_MONTHS = ["2026-01", "2026-02", "2026-03"]
BUILTIN_DICT = json.loads((ROOT / "experiment" / "kpi_dictionary_A_v2.json").read_text(encoding="utf-8"))
REQUIRED_FIELDS = [("formula", "계산식"), ("include_exclude", "포함·제외"),
                   ("period_basis", "기간 기준"), ("unit", "단위")]
BLOCK_TEXT = {"empty": "SQL이 비어 있음", "multiple_statements": "여러 문장이 들어 있음", "not_select": "SELECT로 시작하지 않음",
              "forbidden_keyword": "데이터를 바꾸는 키워드가 들어 있음", "hidden_table": "정답표(ledger)를 읽으려 함"}
UNIT_LABEL = {"net_revenue": "원", "aov": "원", "paid_orders": "건", "refund_rate": "%", "cancel_rate": "%"}
BACKEND = "supabase" if db.enabled() else "sqlite"
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b")

# 질문 속 단어로 지표를 찾는다. strong 은 지표를 거의 확정하는 단어, weak 는 보조 단서.
KEYWORDS = {
    "net_revenue": {"strong": ["매출", "순매출", "매출액", "벌었", "벌어"], "weak": ["판매금액", "수익"]},
    "paid_orders": {"strong": ["주문수", "주문건수", "결제건수", "팔린주문", "유효주문"], "weak": ["주문몇", "몇건", "건수"]},
    "aov": {"strong": ["객단가", "평균주문", "주문하나당", "주문당"], "weak": []},
    "refund_rate": {"strong": ["환불률", "환불율", "환불비율", "환불비중"], "weak": ["환불"]},
    "cancel_rate": {"strong": ["취소율", "취소비율"], "weak": ["취소"]},
}


# ---------------------------------------------------------------- 기간 해석
RANGE_RE = re.compile(r"(\d{1,2})월?(?:[~∼\-–]|부터)(\d{1,2})월")


def parse_months(text):
    """질문에서 월을 찾는다. 반환: {months, invalid, note}. 데이터는 2026-01~03 뿐이다.

    범위("1~2월", "1월부터 3월")는 펼치고, 범위가 아닌 월은 질문에 나온 만큼 모두 합친다
    ("1월, 2월, 3월"이나 "1~2월과 3월"에서 뒤쪽 월이 빠지지 않게)."""
    t = text.replace(" ", "")
    nums, note = set(), ""
    if "분기" in t:
        nums, note = {1, 2, 3}, "1분기"
    else:
        for m in RANGE_RE.finditer(t):
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            nums.update(range(a, b + 1))
            note = "범위"
        rest = RANGE_RE.sub(" ", t)  # 범위로 이미 읽은 부분은 빼고 남은 월을 모은다
        nums.update(int(x) for x in re.findall(r"(\d{1,2})월", rest))
        if not note and len(nums) > 1:
            note = "여러 달"
    months, invalid = [], []
    for n in sorted(nums):
        label = f"2026-{n:02d}"
        (months if label in VALID_MONTHS else invalid).append(label)
    return {"months": months, "invalid": invalid, "note": note}


# ---------------------------------------------------------------- 지표 찾기
def detect_metrics(question):
    t = question.replace(" ", "")
    hits, names = {}, {k: m["name"] for k, m in BUILTIN_DICT["metrics"].items()}
    for key, kw in KEYWORDS.items():
        strong = [w for w in kw["strong"] if w in t]
        weak = [w for w in kw["weak"] if w in t]
        if strong or weak:
            hits[key] = {"strong": strong, "weak": weak}
    # 사용자가 정의한 지표도 이름과 다른 말로 찾는다 (정의해 두고도 다시 '사전에 없음'이 되지 않게)
    for c in _load_custom()["metrics"]:
        names[c["key"]] = c["name"]
        matched = [n for n in [c["name"], *c.get("aliases", [])] if n and n.replace(" ", "") in t]
        if matched:
            hits[c["key"]] = {"strong": matched, "weak": []}
    strong_keys = [k for k, v in hits.items() if v["strong"]]
    keys = strong_keys if strong_keys else list(hits)
    return [{"key": k, "name": names[k], "matched": hits[k]["strong"] + hits[k]["weak"]} for k in keys]


def intake(question):
    """F1/F2: 질문을 해석해 지표 후보와 기간을 돌려준다."""
    question = (question or "").strip()
    if not question:
        raise ValueError("질문을 입력해 주세요.")
    if len(question) > 300:
        raise ValueError("질문은 300자 이내로 입력해 주세요.")
    cands = detect_metrics(question)
    p = parse_months(question)
    status = "unknown" if not cands else ("ok" if len(cands) == 1 else "ambiguous")
    return {"question": question, "candidates": cands, "status": status, "months": p["months"],
            "invalid_months": p["invalid"], "period_missing": not p["months"] and not p["invalid"]}


# ---------------------------------------------------------------- KPI 사전 (F3)
_lock = threading.RLock()  # 서버는 요청을 동시에 처리하므로, 파일을 읽고 고쳐 쓰는 구간을 한 번에 하나씩만 실행한다


def _custom_path():
    return DATA_DIR / "custom_kpis.json"


def _atomic_write(path, text):
    """임시 파일에 쓴 뒤 교체한다. 쓰는 도중 멈춰도 원래 파일이 깨지지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_custom():
    p = _custom_path()
    if not p.exists():
        return {"metrics": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("metrics"), list):
            raise ValueError("형식이 올바르지 않음")
        return data
    except ValueError:
        # 깨진 파일 하나 때문에 내장 지표까지 못 쓰게 되지 않도록, 옆으로 치워 두고 빈 사전으로 계속한다
        backup = p.with_name(f"custom_kpis.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
        os.replace(p, backup)
        sys.stderr.write(f"사용자 정의 사전 파일이 깨져 {backup.name} 으로 옮겼습니다.\n")
        return {"metrics": []}


def _builtin_view(key, m):
    return {"key": key, "name": m["name"], "aliases": m["aliases"], "formula": m["formula"],
            "include_exclude": m["include_exclude"], "period_basis": m["period_basis"], "unit": m["unit"],
            "source": "builtin", "version": f"사전 {BUILTIN_DICT['version']}", "has_reference": True, "history": []}


def dictionary():
    items = [_builtin_view(k, m) for k, m in BUILTIN_DICT["metrics"].items()]
    for c in _load_custom()["metrics"]:
        items.append({**c, "source": "custom", "version": f"v{c['version']}", "has_reference": False})
    return items


def get_metric(key):
    for m in dictionary():
        if m["key"] == key:
            return m
    raise ValueError("사전에 없는 지표입니다. 먼저 정의해 주세요.")


def save_definition(name, aliases, formula, include_exclude, period_basis, unit):
    """필수 4항목이 모두 채워져야 저장된다. 같은 이름이면 버전이 올라간다."""
    name = (name or "").strip()
    fields = {"formula": formula, "include_exclude": include_exclude, "period_basis": period_basis, "unit": unit}
    missing = [label for k, label in REQUIRED_FIELDS if not (fields[k] or "").strip()]
    if not name:
        missing.insert(0, "지표 이름")
    if missing:
        raise ValueError("비어 있는 항목이 있습니다: " + ", ".join(missing))
    if any(len(str(v)) > 600 for v in list(fields.values()) + [name]):
        raise ValueError("각 항목은 600자 이내로 입력해 주세요.")
    builtin_names = {m["name"] for m in BUILTIN_DICT["metrics"].values()}
    if name in builtin_names:
        raise ValueError(f"'{name}'은(는) 이미 사전에 있는 지표입니다.")
    key = "custom_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    now = datetime.now().isoformat(timespec="seconds")
    alias_list = [a.strip() for a in (aliases or []) if a and a.strip()][:8]
    entry = {"key": key, "name": name, "aliases": alias_list, "formula": formula.strip(),
             "include_exclude": include_exclude.strip(), "period_basis": period_basis.strip(),
             "unit": unit.strip(), "version": 1, "created_at": now, "history": []}
    with _lock:  # 읽고 → 고치고 → 쓰는 구간 전체를 보호해 동시 저장이 서로 덮어쓰지 않게 한다
        data = _load_custom()
        for i, old in enumerate(data["metrics"]):
            if old["key"] == key:
                entry["version"] = old["version"] + 1
                entry["history"] = old["history"] + [{k: old[k] for k in
                                  ("version", "formula", "include_exclude", "period_basis", "unit", "created_at")}]
                data["metrics"][i] = entry
                break
        else:
            data["metrics"].append(entry)
        _atomic_write(_custom_path(), json.dumps(data, ensure_ascii=False, indent=2))
    return {**entry, "source": "custom", "version": f"v{entry['version']}", "has_reference": False}


# ---------------------------------------------------------------- 실행 (F4, F5)
def _prompt_dictionary_text():
    merged = {"metrics": dict(BUILTIN_DICT["metrics"])}
    for c in _load_custom()["metrics"]:
        merged["metrics"][c["key"]] = {k: c[k] for k in ("name", "aliases", "formula", "include_exclude", "period_basis", "unit")}
    return R.render_dictionary(merged)


def llm_key_status():
    return bool(os.environ.get("NVIDIA_API_KEY"))


_clients, _clients_lock = {}, threading.Lock()


def _make_client(model):
    """모델별로 클라이언트를 하나만 만들어 공유한다. 호출 속도 제한(rpm)이 요청마다가 아니라 전체에 적용되게 한다."""
    from llm_client import LLMClient
    with _clients_lock:
        if model not in _clients:
            _clients[model] = LLMClient(model, temperature=0.0, max_tokens=4096, rpm=20, timeout=180, max_retries=1)
        return _clients[model]


def _fmt_ms(t0):
    return round((time.perf_counter() - t0) * 1000)


def _same(metric_key, a, b):
    if a is None or b is None:
        return False
    return abs(a - b) <= R.TOLERANCE[metric_key]


def gold_sql(metric_key, months):
    """기준 계산식. 같은 정의를 데이터베이스 문법에 맞게 돌려준다."""
    return db.gold_sql(metric_key, months) if BACKEND == "supabase" else _sqlite_gold_sql(metric_key, months)


def _execute(sql):
    return db.execute_readonly(sql) if BACKEND == "supabase" else R.execute_readonly(sql)


def _ledger_total(months):
    if BACKEND == "supabase":
        return db.ledger_total(months)
    import sqlite3
    con = sqlite3.connect(f"file:{R.DB}?mode=ro", uri=True)
    try:
        ledger = dict(con.execute("SELECT period_month, net_revenue_ex_vat FROM ledger"))
    finally:
        con.close()
    if any(m not in ledger for m in months):
        return None
    return sum(ledger[m] for m in months)


def reconcile(metric, months, mode, value, reference_value):
    """F6: 대사. 각 검사를 {name,status,message}로 모아 전체 판정을 돌려준다.
    PASS 모두 통과 / FLAG 불일치 / UNVERIFIED 정답 출처 없음 / INFO 기준 계산식으로 계산됨."""
    checks = []
    key = metric["key"]
    if key == "net_revenue":
        total = _ledger_total(months)
        if total is not None and value is not None:
            ok = abs(value - total) < 0.5  # 정확한 정답 출처가 있으므로 허용 오차 0 (D-024)
            checks.append({"name": "원장 대사", "status": "PASS" if ok else "FLAG",
                           "message": ("원장 합계와 정확히 일치합니다." if ok else
                                       f"원장 합계 {total:,.0f}원과 {value - total:+,.0f}원 차이가 납니다.")})
    if metric["has_reference"] and mode == "ai":
        ok = _same(key, value, reference_value)
        checks.append({"name": "기준 계산식 대조", "status": "PASS" if ok else "FLAG",
                       "message": ("사람이 검토한 기준 계산식의 값과 같습니다." if ok else
                                   f"기준 계산식의 값({_fmt_value(key, reference_value)})과 다릅니다.")})
    if not checks and metric["has_reference"]:
        checks.append({"name": "기준 계산식", "status": "INFO",
                       "message": "사람이 검토한 기준 계산식으로 계산했습니다. 이 지표에는 별도의 정답 출처(원장)가 없습니다."})
    if not metric["has_reference"]:
        checks.append({"name": "검증 불가", "status": "UNVERIFIED",
                       "message": "이 지표에는 정답 출처가 없어 '검증됨'을 붙일 수 없습니다. AI가 만든 숫자이므로 사람이 직접 확인하세요."})
    statuses = [c["status"] for c in checks]
    overall = ("FLAG" if "FLAG" in statuses else "PASS" if "PASS" in statuses
               else "UNVERIFIED" if "UNVERIFIED" in statuses else "INFO")
    return {"overall": overall, "checks": checks}


def short_unit(text):
    """사용자가 적은 단위 설명('%, 소수 둘째 자리 반올림')에서 표시용 단위('%')만 뽑는다."""
    m = re.match(r"\s*([^,，;(\s]+)", text or "")
    return m.group(1) if m else ""


def _fmt_value(key, v, unit=None):
    if v is None:
        return "-"
    unit = unit if unit is not None else UNIT_LABEL.get(key, "")
    if unit == "%":
        return f"{v:,.2f}%"
    num = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return f"{num}{unit}"


def run_query(question, metric_key, months, mode="reference", model=None, user_id=None):
    """F4~F6: 정의된 지표만 실행한다. 사전에 없는 지표는 거부한다."""
    metric = get_metric(metric_key)
    months = sorted({m for m in months if isinstance(m, str) and m in VALID_MONTHS})  # 중복 제거(원장 합계가 두 번 더해지지 않게)
    if not months:
        raise ValueError("데이터가 있는 기간(2026년 1~3월)을 하나 이상 선택해 주세요.")
    if mode not in ("reference", "ai"):
        raise ValueError("실행 방식이 올바르지 않습니다.")
    if mode == "reference" and not metric["has_reference"]:
        raise ValueError("이 지표에는 검증된 기준 계산식이 없습니다. 'AI가 SQL 생성' 방식을 선택하세요.")
    model = (model or DEFAULT_MODEL).strip()
    rec = {"id": datetime.now().strftime("%Y%m%d-%H%M%S-") + hashlib.sha1(os.urandom(8)).hexdigest()[:4],
           "ts": datetime.now().isoformat(timespec="seconds"), "question": question, "metric_key": metric_key,
           "metric_name": metric["name"], "definition_version": metric["version"], "months": months,
           "mode": mode, "model": model if mode == "ai" else None, "sql": None, "value": None,
           "unit": UNIT_LABEL.get(metric_key) or short_unit(metric["unit"]), "elapsed_ms": None, "status": "OK", "error": None,
           "reference_value": None, "checks": [], "overall": "ERROR"}
    t0 = time.perf_counter()
    try:
        reference_value = None
        if mode == "reference":
            rec["sql"] = gold_sql(metric_key, months)
        else:
            if not llm_key_status():
                raise RuntimeError("NVIDIA_API_KEY 가 설정되어 있지 않습니다. 앱을 시작한 창에서 키를 설정한 뒤 다시 시작하세요.")
            q = f"{question}\n(확정한 기간: {', '.join(months)})"
            schema = db.schema_text() if BACKEND == "supabase" else R.load_schema()
            system = (R.SYSTEM_PROMPT.replace("SQLite", "PostgreSQL") + " " + db.SYSTEM_NOTE) if BACKEND == "supabase" else R.SYSTEM_PROMPT
            user = R.build_user_prompt("B2", schema, _prompt_dictionary_text(), q)
            resp = _make_client(model).chat(system, user)
            rec["sql"] = R.extract_sql(resp["text"])
            block = R.safety_check(rec["sql"])
            if block:
                rec["status"] = "BLOCKED"
                raise RuntimeError(f"안전 검사에서 차단되었습니다: {BLOCK_TEXT.get(block, block)}. 읽기 전용 SELECT 한 문장만 실행합니다.")
        rec["value"], shape = _execute(rec["sql"])
        rec["elapsed_ms"] = _fmt_ms(t0)  # 대조용 기준값 계산 시간은 포함하지 않는다
        if rec["value"] is None:
            raise RuntimeError(f"결과가 숫자 하나(1행 1열)가 아닙니다({shape}).")
        if metric["has_reference"]:
            # 기준 계산식 방식이면 방금 실행한 값이 곧 기준값이라 다시 실행하지 않는다
            reference_value = rec["value"] if mode == "reference" else _execute(gold_sql(metric_key, months))[0]
            rec["reference_value"] = reference_value
        rec.update(reconcile(metric, months, mode, rec["value"], reference_value))
    except Exception as e:  # noqa: BLE001  화면에 이유를 보여 주고 기록한다 (F9)
        if rec["status"] == "OK":
            rec["status"] = "ERROR"
        rec["error"] = ((db.friendly_error(e) if BACKEND == "supabase" else None) or str(e))[:300]
        rec["elapsed_ms"] = rec["elapsed_ms"] or _fmt_ms(t0)
        rec["overall"] = "ERROR"
    _save_run(rec, user_id)
    return rec


# ---------------------------------------------------------------- 기록 (F9)
def _history_path():
    return DATA_DIR / "history.jsonl"


def _save_run(rec, user_id=None):
    """Supabase 모드면 app.query_runs 에, 아니면 로컬 기록 파일에 남긴다."""
    if BACKEND == "supabase":
        db.record_run(rec, user_id)
    else:
        _append_history(rec)


def _append_history(rec):
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _history_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _iter_history():
    """기록 파일을 한 줄씩 읽는다(전체를 메모리에 올리지 않음). 쓰다 만 깨진 줄은 건너뛴다."""
    p = _history_path()
    if not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def history(limit=50, user_id=None):
    if BACKEND == "supabase":
        return db.history(user_id, limit) if user_id else []  # 실행 기록은 로그인한 본인 것만 본다
    return list(reversed(collections.deque(_iter_history(), maxlen=limit)))


def get_run(run_id, user_id=None):
    if BACKEND == "supabase":
        found = db.get_run(run_id, user_id) if user_id else None
        if found is None:
            raise ValueError("해당 실행 기록을 찾을 수 없습니다.")
        return found
    found = None
    for r in _iter_history():  # 기록 개수 상한 없이 전체에서 찾는다
        if r.get("id") == run_id:
            found = r
    if found is None:
        raise ValueError("해당 실행 기록을 찾을 수 없습니다.")
    return found


# ---------------------------------------------------------------- 리포트 (F7)
def _md_cell(text):
    """마크다운 표 칸에 넣을 글: 줄바꿈은 공백 하나로, '|'는 이스케이프해서 표가 깨지지 않게 한다."""
    return " ".join(str(text).split()).replace("|", chr(92) + "|")


BADGE_TEXT = {"PASS": "통과", "FLAG": "경고(불일치)", "UNVERIFIED": "검증 불가", "INFO": "기준 계산식으로 계산", "ERROR": "오류"}


def make_report(run_id, user_id=None):
    r = get_run(run_id, user_id)
    metric = get_metric(r["metric_key"])
    mode_text = ("검증된 기준 계산식 실행" if r["mode"] == "reference" else f"AI가 SQL 생성 (모델: {r['model']})")
    lines = [f"# PoC 리포트 — {r['metric_name']}", "",
             f"- 생성 시각: {r['ts']}", f"- 실행 ID: `{r['id']}`", f"- 질문: {r['question']}",
             f"- 기간: {', '.join(r['months'])}", f"- 실행 방식: {mode_text}",
             f"- 정의 버전: {r['definition_version']}", "",
             "## 결과", "",
             f"**{_fmt_value(r['metric_key'], r['value'], r.get('unit'))}**" if r["value"] is not None else "결과 없음 (오류)",
             "", f"- 실행 시간: {r['elapsed_ms']} ms",
             f"- 전체 판정: **{BADGE_TEXT.get(r['overall'], r['overall'])}**", ""]
    if r["error"]:
        lines += [f"- 오류: {r['error']}", ""]
    lines += ["## 대사(검증) 결과", ""]
    for c in r["checks"]:
        lines.append(f"- [{BADGE_TEXT.get(c['status'], c['status'])}] **{c['name']}**: {c['message']}")
    if not r["checks"]:
        lines.append("- 수행된 검사가 없습니다.")
    lines += ["", "## 사용한 정의 (KPI 사전)", "",
              "| 항목 | 내용 |", "|---|---|", f"| 계산식 | {_md_cell(metric['formula'])} |",
              f"| 포함·제외 | {_md_cell(metric['include_exclude'])} |", f"| 기간 기준 | {_md_cell(metric['period_basis'])} |",
              f"| 단위 | {_md_cell(metric['unit'])} |", "", "## 실행한 SQL", "", "```sql", r["sql"] or "(생성되지 않음)", "```", "",
              "## 읽는 법과 한계", "",
              "- 이 숫자는 합성(가짜) 주문 데이터(2026-01~03)에서 계산한 값입니다. 실제 고객 데이터가 아닙니다.",
              "- '통과'는 정답 출처(원장 또는 사람이 검토한 기준 계산식)와 같다는 뜻이며, 정의 자체가 옳다는 뜻은 아닙니다.",
              "- '검증 불가'는 정답 출처가 없다는 뜻입니다. 숫자가 맞다는 보장이 없으니 사람이 확인해야 합니다.",
              "- 근거 문서: docs/05-data-design.md, docs/07-experiment-results.md, docs/09-experiment-v2-results.md"]
    text = "\n".join(lines) + "\n"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{r['id']}-{r['metric_key']}.md"
    (REPORT_DIR / fname).write_text(text, encoding="utf-8")
    return {"filename": fname, "path": str((REPORT_DIR / fname).relative_to(ROOT)) if REPORT_DIR.is_relative_to(ROOT) else str(REPORT_DIR / fname),
            "markdown": text}
