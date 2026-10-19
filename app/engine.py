"""PoC Desk 엔진: 질문 해석, KPI 사전, 실행, 대사, 리포트.

HTTP와 분리해 두어 단독으로 테스트할 수 있다. 설계는 docs/03-prd.md 의 F1~F7, F9 를 따른다.

실행 방식 두 가지
  reference  사람이 검토한 지표 계산식(experiment/gold.py)을 그대로 실행한다. (시맨틱 레이어에 가까운 방식)
  ai         LLM이 SQL을 만든다. 테이블 구조와 KPI 사전만 보내고 거래 행은 보내지 않는다. 결과는 기준 계산식·원장과 대조한다.

중요: 정답 출처가 없는 지표(사용자가 새로 정의한 지표)는 '검증됨'을 붙이지 않고 '검증 불가'로 표시한다 (결정 D-025).
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(ROOT / "experiment"))

import run_experiment as R  # noqa: E402  (안전 검사, 읽기 전용 실행, 프롬프트 구성을 실험과 똑같이 쓴다)
from gold import gold_sql  # noqa: E402

DATA_DIR = Path(os.environ.get("POC_DESK_DATA_DIR", APP_DIR / "data"))
REPORT_DIR = Path(os.environ.get("POC_DESK_REPORT_DIR", APP_DIR / "reports"))
VALID_MONTHS = ["2026-01", "2026-02", "2026-03"]
BUILTIN_DICT = json.loads((ROOT / "experiment" / "kpi_dictionary_A_v2.json").read_text(encoding="utf-8"))
REQUIRED_FIELDS = [("formula", "계산식"), ("include_exclude", "포함·제외"),
                   ("period_basis", "기간 기준"), ("unit", "단위")]
BLOCK_TEXT = {"empty": "SQL이 비어 있음", "multiple_statements": "여러 문장이 들어 있음", "not_select": "SELECT로 시작하지 않음",
              "forbidden_keyword": "데이터를 바꾸는 키워드가 들어 있음", "hidden_table": "정답표(ledger)를 읽으려 함"}
UNIT_LABEL = {"net_revenue": "원", "aov": "원", "paid_orders": "건", "refund_rate": "%", "cancel_rate": "%"}
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
def parse_months(text):
    """질문에서 월을 찾는다. 반환: {months, invalid, note}. 데이터는 2026-01~03 뿐이다."""
    t = text.replace(" ", "")
    nums, note = [], ""
    if "분기" in t:
        nums, note = [1, 2, 3], "1분기"
    else:
        m = re.search(r"(\d{1,2})월?[~∼\-–](\d{1,2})월", t) or re.search(r"(\d{1,2})월부터(\d{1,2})월", t)
        pair = re.search(r"(\d{1,2})월(?:과|와|하고|및|,)(\d{1,2})월", t)
        if m:
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            nums, note = list(range(a, b + 1)), "범위"
        elif pair:
            nums, note = sorted({int(pair.group(1)), int(pair.group(2))}), "두 달"
        else:
            nums = sorted({int(x) for x in re.findall(r"(\d{1,2})월", t)})
    months, invalid = [], []
    for n in nums:
        label = f"2026-{n:02d}"
        (months if label in VALID_MONTHS else invalid).append(label)
    return {"months": months, "invalid": invalid, "note": note}


# ---------------------------------------------------------------- 지표 찾기
def detect_metrics(question):
    t = question.replace(" ", "")
    hits = {}
    for key, kw in KEYWORDS.items():
        strong = [w for w in kw["strong"] if w in t]
        weak = [w for w in kw["weak"] if w in t]
        if strong or weak:
            hits[key] = {"strong": strong, "weak": weak}
    strong_keys = [k for k, v in hits.items() if v["strong"]]
    keys = strong_keys if strong_keys else list(hits)
    return [{"key": k, "name": BUILTIN_DICT["metrics"][k]["name"],
             "matched": hits[k]["strong"] + hits[k]["weak"]} for k in keys]


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
def _custom_path():
    return DATA_DIR / "custom_kpis.json"


def _load_custom():
    p = _custom_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"metrics": []}


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
    data = _load_custom()
    now = datetime.now().isoformat(timespec="seconds")
    alias_list = [a.strip() for a in (aliases or []) if a and a.strip()][:8]
    entry = {"key": key, "name": name, "aliases": alias_list, "formula": formula.strip(),
             "include_exclude": include_exclude.strip(), "period_basis": period_basis.strip(),
             "unit": unit.strip(), "version": 1, "created_at": now, "history": []}
    for i, old in enumerate(data["metrics"]):
        if old["key"] == key:
            entry["version"] = old["version"] + 1
            entry["history"] = old["history"] + [{k: old[k] for k in
                              ("version", "formula", "include_exclude", "period_basis", "unit", "created_at")}]
            data["metrics"][i] = entry
            break
    else:
        data["metrics"].append(entry)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _custom_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**entry, "source": "custom", "version": f"v{entry['version']}", "has_reference": False}


# ---------------------------------------------------------------- 실행 (F4, F5)
def _prompt_dictionary_text():
    merged = {"metrics": dict(BUILTIN_DICT["metrics"])}
    for c in _load_custom()["metrics"]:
        merged["metrics"][c["key"]] = {k: c[k] for k in ("name", "aliases", "formula", "include_exclude", "period_basis", "unit")}
    return R.render_dictionary(merged)


def llm_key_status():
    return bool(os.environ.get("NVIDIA_API_KEY"))


def _make_client(model):
    from llm_client import LLMClient
    return LLMClient(model, temperature=0.0, max_tokens=4096, rpm=20, timeout=180, max_retries=1)


def _fmt_ms(t0):
    return round((time.perf_counter() - t0) * 1000)


def _same(metric_key, a, b):
    if a is None or b is None:
        return False
    return abs(a - b) <= R.TOLERANCE[metric_key]


def _ledger_total(months):
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


def _fmt_value(key, v):
    if v is None:
        return "-"
    if key in ("refund_rate", "cancel_rate"):
        return f"{v:,.2f}%"
    if key == "paid_orders":
        return f"{v:,.0f}건"
    return f"{v:,.0f}원"


def run_query(question, metric_key, months, mode="reference", model=None):
    """F4~F6: 정의된 지표만 실행한다. 사전에 없는 지표는 거부한다."""
    metric = get_metric(metric_key)
    months = [m for m in months if m in VALID_MONTHS]
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
           "unit": UNIT_LABEL.get(metric_key, metric["unit"]), "elapsed_ms": None, "status": "OK", "error": None,
           "reference_value": None, "checks": [], "overall": "ERROR"}
    t0 = time.perf_counter()
    try:
        reference_value = None
        if metric["has_reference"]:
            reference_value, _ = R.execute_readonly(gold_sql(metric_key, months))
            rec["reference_value"] = reference_value
        if mode == "reference":
            rec["sql"] = gold_sql(metric_key, months)
        else:
            if not llm_key_status():
                raise RuntimeError("NVIDIA_API_KEY 가 설정되어 있지 않습니다. 앱을 시작한 창에서 키를 설정한 뒤 다시 시작하세요.")
            q = f"{question}\n(확정한 기간: {', '.join(months)})"
            user = R.build_user_prompt("B2", R.load_schema(), _prompt_dictionary_text(), q)
            resp = _make_client(model).chat(R.SYSTEM_PROMPT, user)
            rec["sql"] = R.extract_sql(resp["text"])
            block = R.safety_check(rec["sql"])
            if block:
                rec["status"] = "BLOCKED"
                raise RuntimeError(f"안전 검사에서 차단되었습니다: {BLOCK_TEXT.get(block, block)}. 읽기 전용 SELECT 한 문장만 실행합니다.")
        rec["value"], shape = R.execute_readonly(rec["sql"])
        rec["elapsed_ms"] = _fmt_ms(t0)
        if rec["value"] is None:
            raise RuntimeError(f"결과가 숫자 하나(1행 1열)가 아닙니다({shape}).")
        rec.update(reconcile(metric, months, mode, rec["value"], reference_value))
    except Exception as e:  # noqa: BLE001  화면에 이유를 보여 주고 기록한다 (F9)
        if rec["status"] == "OK":
            rec["status"] = "ERROR"
        rec["error"] = str(e)[:300]
        rec["elapsed_ms"] = rec["elapsed_ms"] or _fmt_ms(t0)
        rec["overall"] = "ERROR"
    _append_history(rec)
    return rec


# ---------------------------------------------------------------- 기록 (F9)
def _history_path():
    return DATA_DIR / "history.jsonl"


def _append_history(rec):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _history_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def history(limit=50):
    p = _history_path()
    if not p.exists():
        return []
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return list(reversed(rows[-limit:]))


def get_run(run_id):
    for r in history(10000):
        if r["id"] == run_id:
            return r
    raise ValueError("해당 실행 기록을 찾을 수 없습니다.")


# ---------------------------------------------------------------- 리포트 (F7)
BADGE_TEXT = {"PASS": "통과", "FLAG": "경고(불일치)", "UNVERIFIED": "검증 불가", "INFO": "기준 계산식으로 계산", "ERROR": "오류"}


def make_report(run_id):
    r = get_run(run_id)
    metric = get_metric(r["metric_key"])
    mode_text = ("검증된 기준 계산식 실행" if r["mode"] == "reference" else f"AI가 SQL 생성 (모델: {r['model']})")
    lines = [f"# PoC 리포트 — {r['metric_name']}", "",
             f"- 생성 시각: {r['ts']}", f"- 실행 ID: `{r['id']}`", f"- 질문: {r['question']}",
             f"- 기간: {', '.join(r['months'])}", f"- 실행 방식: {mode_text}",
             f"- 정의 버전: {r['definition_version']}", "",
             "## 결과", "",
             f"**{_fmt_value(r['metric_key'], r['value'])}**" if r["value"] is not None else "결과 없음 (오류)",
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
              f"| 항목 | 내용 |", "|---|---|", f"| 계산식 | {metric['formula']} |",
              f"| 포함·제외 | {metric['include_exclude']} |", f"| 기간 기준 | {metric['period_basis']} |",
              f"| 단위 | {metric['unit']} |", "", "## 실행한 SQL", "", "```sql", r["sql"] or "(생성되지 않음)", "```", "",
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
