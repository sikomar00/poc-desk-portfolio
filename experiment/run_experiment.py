"""H1 실험 실행기: 조건 A(테이블 구조만) vs 조건 B(테이블 구조 + KPI 사전).

흐름: 질문 -> 프롬프트 -> LLM -> SQL 추출 -> 안전 검사 -> 읽기 전용 실행 -> 골드와 채점 -> jsonl 기록

사용 예
  python experiment/run_experiment.py --dry-run                        # 프롬프트만 출력(호출 안 함)
  python experiment/run_experiment.py --mock                           # 가짜 LLM으로 파이프라인·채점 검증
  python experiment/run_experiment.py --model MODEL --limit 4          # 실제 호출 스모크 테스트(4회)
  python experiment/run_experiment.py --model MODEL                    # 본 실험 200회 (중단 후 재실행하면 이어서)
  python experiment/run_experiment.py --summarize experiment/runs/XXX.jsonl

주의: 아래 판정 기준은 06 문서 §1에서 실행 전에 고정한 값이다. 결과를 본 뒤 바꾸지 않는다.
"""
import argparse
import hashlib
import json
import math
import random
import re
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
DB = HERE.parent / "data" / "poc_desk_sample.db"

# ---- 사전 고정된 판정 기준 (06 §1) ----
H1_SUPPORT_ACC_GAIN_PP = 15.0   # B 정확도가 A보다 이만큼(%p) 이상 높고
H1_SUPPORT_B_CONSISTENCY = 0.90  # B 일치율이 이 이상이면 '지지'
H1_REJECT_ACC_GAIN_PP = 5.0     # 정확도 차이가 이 미만이면 '기각'
REPS = 5
SHUFFLE_SEED = 20261019

SUITES = {
    "main": {"questions": "questions_A.json", "conds": {"A": None, "B": "kpi_dictionary_A.json"}},
    "holdout": {"questions": "questions_A_holdout.json",
                "conds": {"B1": "kpi_dictionary_A.json", "B2": "kpi_dictionary_A_v2.json"}},
}

TOLERANCE = {"net_revenue": 0.5, "paid_orders": 0.5, "aov": 1.0, "refund_rate": 0.0100001, "cancel_rate": 0.0100001}

SYSTEM_PROMPT = (
    "당신은 SQLite SQL 작성기입니다. 사용자의 질문에 답하는 SELECT 문 한 개만 작성하세요. "
    "결과는 숫자 하나(1행 1열)여야 합니다. 설명 없이 ```sql 코드 블록 하나만 출력하세요. "
    "데이터를 변경하는 구문은 절대 쓰지 않습니다."
)
COMMON_NOTE = "데이터 기간은 2026-01 ~ 2026-03 입니다. 연도 없이 월만 말하면 2026년으로 해석하세요."
USE_DICT_NOTE = "아래 KPI 사전의 정의를 따라 계산하세요."


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


HIDDEN_TABLES = ("ledger",)  # 정답표(원장): LLM에게 보여 주면 정답을 베낄 수 있으므로 숨긴다


def load_schema():
    """LLM에 줄 테이블 구조. 정답표(ledger)와 SQL 주석(정의 힌트가 들어 있음)은 제외한다."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
                       "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    con.close()
    out = []
    for name, sql in rows:
        if name in HIDDEN_TABLES:
            continue
        sql = re.sub(r"--[^\n]*", "", sql)                  # 주석 제거
        sql = "\n".join(l.rstrip() for l in sql.splitlines() if l.strip())
        out.append(sql + ";")
    return "\n".join(out)


def render_dictionary(d):
    lines = []
    for m in d["metrics"].values():
        lines.append(f"- {m['name']} (별칭: {', '.join(m['aliases'])})\n"
                     f"  계산식: {m['formula']}\n  포함·제외: {m['include_exclude']}\n"
                     f"  기간 기준: {m['period_basis']}\n  단위: {m['unit']}")
    return "\n".join(lines)


def build_user_prompt(cond, schema, dict_text, question):
    parts = [f"[테이블 구조]\n{schema}", COMMON_NOTE]
    if cond != "A":  # B, B1, B2: KPI 사전 제공(어떤 사전인지는 호출하는 쪽이 dict_text로 정한다)
        parts.append(f"[KPI 사전]\n{USE_DICT_NOTE}\n{dict_text}")
    parts.append(f"[질문]\n{question}")
    return "\n\n".join(parts)


# ---- SQL 추출·안전 검사·실행 ----
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|reindex|truncate)\b", re.I)


def extract_sql(text):
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    sql = (m.group(1) if m else text).strip()
    return sql.rstrip(";").strip()


def safety_check(sql):
    """통과하면 None, 막으면 사유 문자열."""
    if not sql:
        return "empty"
    if ";" in sql:
        return "multiple_statements"
    head = sql.lstrip("( \n\t").lower()
    if not (head.startswith("select") or head.startswith("with")):
        return "not_select"
    stripped = re.sub(r"'[^']*'", "''", sql)  # 문자열 안의 단어는 무시
    if FORBIDDEN.search(stripped):
        return "forbidden_keyword"
    if re.search(r"\b(" + "|".join(HIDDEN_TABLES) + r")\b", stripped, re.I):
        return "hidden_table"  # 정답표 접근 시도
    return None


def execute_readonly(sql, timeout_s=5):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    start = time.monotonic()
    con.set_progress_handler(lambda: 1 if time.monotonic() - start > timeout_s else 0, 100000)
    try:
        rows = con.execute(sql).fetchall()
    finally:
        con.close()
    if len(rows) != 1 or len(rows[0]) != 1:
        return None, "bad_shape"
    v = rows[0][0]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None, "not_numeric"
    return float(v), "ok"


def is_match(metric, value, gold):
    return value is not None and abs(value - float(gold)) <= TOLERANCE[metric]


# ---- 모의(Mock) LLM: 파이프라인 검증 전용. 결과를 실험 결과로 쓰면 안 된다 ----
class MockClient:
    temperature = 0.0
    P_OK = {"A": 0.40, "B": 0.85, "B1": 0.50, "B2": 0.80}

    def __init__(self, model="MOCK"):
        self.model = model

    def chat(self, system, user, meta):
        rng = random.Random(f"{self.model}|{meta['run_key']}")
        p_ok = self.P_OK[meta["cond"]]
        r = rng.random()
        if r < 0.02:
            text = "```sql\nDELETE FROM orders\n```"
        elif r < 0.04:
            text = "죄송하지만 SQL을 만들 수 없습니다."
        elif r < p_ok:
            text = f"```sql\n{meta['gold_sql']}\n```"
        else:
            text = "```sql\nSELECT SUM(total_amount) FROM orders\n```"
        return {"text": text, "latency_s": 0.0, "prompt_tokens": None, "completion_tokens": None, "model": "MOCK"}


class AuthAbort(Exception):
    pass


# ---- 집계 ----
def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def consistency(values, tol):
    """같은 질문 반복 결과를 허용 오차로 묶어 최빈 묶음 비율. 실패(None)는 서로 다른 값으로 취급한다."""
    clusters = []  # [대표값, 개수]
    for v in values:
        if v is None:
            clusters.append([object(), 1])
            continue
        for c in clusters:
            if isinstance(c[0], float) and abs(c[0] - v) <= tol:
                c[1] += 1
                break
        else:
            clusters.append([v, 1])
    return max(c[1] for c in clusters) / len(values)


def summarize(path):
    recs = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    meta = next(r for r in recs if r.get("type") == "meta")
    runs = [r for r in recs if r.get("type") == "run"]
    if meta.get("suite", "main") != "main":
        sys.exit("이 기록은 보류 질문(holdout) 실험입니다. 사전 지정 평가: python experiment/evaluate_v2.py")
    print(f"파일: {path}\n모델={meta['model']} 온도={meta['temperature']} 실행 수={len(runs)}"
          f"{'  *** MOCK: 실험 결과가 아님 ***' if meta['model'] == 'MOCK' else ''}\n")
    res = {}
    for cond in ("A", "B"):
        rs = [r for r in runs if r["cond"] == cond]
        k = sum(r["correct"] for r in rs)
        lo, hi = wilson(k, len(rs))
        by_q = defaultdict(list)
        for r in rs:
            by_q[r["qid"]].append(r)
        cons = [consistency([x["value"] for x in v], TOLERANCE[v[0]["metric"]]) for v in by_q.values()]
        status = Counter(r["status"] for r in rs)
        res[cond] = {"acc": k / len(rs) if rs else 0, "cons": sum(cons) / len(cons) if cons else 0}
        print(f"조건 {cond}: 정확도 {k}/{len(rs)} = {res[cond]['acc']*100:.1f}% (95% 구간 {lo*100:.0f}~{hi*100:.0f}%)  "
              f"일치율 {res[cond]['cons']*100:.1f}%")
        print(f"   상태: {dict(status)}")
        fin = Counter(r.get("finish_reason") for r in rs)
        n_len = fin.get("length", 0)
        if n_len:
            print(f"   토큰 한도로 잘린 호출(length): {n_len}/{len(rs)} = {n_len/len(rs)*100:.1f}% (오답으로 계산됨)")
    print("\n[지표별 정확도 A -> B]")
    for metric in TOLERANCE:
        row = []
        for cond in ("A", "B"):
            rs = [r for r in runs if r["cond"] == cond and r["metric"] == metric]
            row.append(f"{sum(r['correct'] for r in rs)}/{len(rs)}")
        print(f"  {metric:12s} {row[0]:>7s} -> {row[1]:>7s}")
    gain = (res["B"]["acc"] - res["A"]["acc"]) * 100
    print(f"\n[H1 판정 (사전 고정 기준)] 정확도 차이(B-A) = {gain:+.1f}%p, B 일치율 = {res['B']['cons']*100:.1f}%")
    if gain >= H1_SUPPORT_ACC_GAIN_PP and res["B"]["cons"] >= H1_SUPPORT_B_CONSISTENCY:
        print("  -> 지지 (정확도 차이 ≥ 15%p 이고 B 일치율 ≥ 90%)")
    elif gain < H1_REJECT_ACC_GAIN_PP:
        print("  -> 기각 (정확도 차이 < 5%p)")
    else:
        print("  -> 불명 (두 기준 사이). 표본을 늘릴지 판단")


# ---- 실행 ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--rpm", type=int, default=30)
    ap.add_argument("--max-tokens", type=int, default=4096, help="추론형 모델은 생각에 토큰을 쓰므로 넉넉히")
    ap.add_argument("--timeout", type=int, default=240, help="호출 1회 제한 시간(초)")
    ap.add_argument("--workers", type=int, default=1, help="동시에 보낼 호출 수(응답이 느릴 때 시간 단축). 호출 시작 간격은 --rpm 으로 계속 제한됨")
    ap.add_argument("--limit", type=int, help="앞에서부터 N회만 실행(스모크 테스트)")
    ap.add_argument("--run-id", help="이어서 실행하려면 기존 실행 ID")
    ap.add_argument("--merge-system", action="store_true", help="system 역할을 user 앞에 합쳐 보낸다(미지원 모델용)")
    ap.add_argument("--suite", choices=list(SUITES), default="main",
                    help="main: 본 실험(조건 A·B, 질문 20개) / holdout: 사전 v2 보류 질문 실험(조건 B1·B2, 질문 10개)")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--mock-model", default="MOCK", help="모의 실행에서 모델 이름표(교차 모델 평가 시험용)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summarize")
    a = ap.parse_args()
    if a.summarize:
        return summarize(a.summarize)

    suite = SUITES[a.suite]
    questions = json.loads((HERE / suite["questions"]).read_text(encoding="utf-8"))
    dict_texts = {c: (render_dictionary(json.loads((HERE / f).read_text(encoding="utf-8"))) if f else None)
                  for c, f in suite["conds"].items()}
    conds = list(suite["conds"])
    schema = load_schema()

    if a.dry_run:
        q = questions[1]
        for cond in conds:
            print(f"===== 조건 {cond} / {q['id']} =====\n[system]\n{SYSTEM_PROMPT}\n\n[user]\n"
                  f"{build_user_prompt(cond, schema, dict_texts[cond], q['question'])}\n")
        return

    plan = [(c, q, r) for q in questions for r in range(1, REPS + 1) for c in conds]
    random.Random(SHUFFLE_SEED).shuffle(plan)  # 조건이 시간순으로 한쪽에 몰리지 않게 섞는다
    if a.limit:
        plan = plan[:a.limit]

    if a.mock:
        client, outdir = MockClient(a.mock_model), HERE / "runs_mock"
    else:
        if not a.model:
            sys.exit("--model 이 필요합니다 (예: python experiment/llm_client.py models 로 확인)")
        from llm_client import LLMClient, LLMError  # noqa: E402
        client = LLMClient(a.model, a.temperature, max_tokens=a.max_tokens, rpm=a.rpm,
                           timeout=a.timeout, merge_system=a.merge_system)
        outdir = HERE / "runs"
    outdir.mkdir(exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9.-]+", "_", client.model.split("/")[-1])[:24]  # 같은 초에 다른 모델을 시작해도 파일이 겹치지 않게
    run_id = a.run_id or datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{slug}" + ("-mock" if a.mock else "")
    path = outdir / f"{run_id}.jsonl"

    done = set()
    if path.exists():
        for l in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if r.get("type") == "run" and r.get("status") != "llm_error":  # 서비스 장애로 실패한 호출은 이어서 실행할 때 다시 시도
                done.add(r["run_key"])
    else:
        meta = {"type": "meta", "run_id": run_id, "started_at": datetime.now().isoformat(timespec="seconds"),
                "model": client.model, "temperature": a.temperature if not a.mock else 0.0, "rpm": a.rpm,
                "max_tokens": a.max_tokens if not a.mock else None,
                "system_prompt_hash": sha(SYSTEM_PROMPT),
                "prompt_template_hash": sha(COMMON_NOTE + USE_DICT_NOTE + build_user_prompt("B", "{schema}", "{dict}", "{q}")),
                "schema_hash": sha(schema), "suite": a.suite, "conditions": conds,
                "dictionary_hashes": {c: (sha(t) if t else None) for c, t in dict_texts.items()},
                "questions_hash": sha(json.dumps(questions, ensure_ascii=False, sort_keys=True)),
                "reps": REPS, "tolerance": TOLERANCE}
        if dict_texts.get("B"):
            meta["dictionary_hash"] = sha(dict_texts["B"])  # 본 실험 기록과의 호환
        path.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")

    todo = [(c, q, r) for c, q, r in plan if f"{c}|{q['id']}|{r}" not in done]
    print(f"실행 ID={run_id}  남은 호출 {len(todo)}/{len(plan)}  모델={client.model}  -> {path}")
    write_lock = threading.Lock()

    def do_one(task):
        cond, q, rep = task
        run_key = f"{cond}|{q['id']}|{rep}"
        user = build_user_prompt(cond, schema, dict_texts[cond], q["question"])
        rec = {"type": "run", "run_key": run_key, "cond": cond, "qid": q["id"], "rep": rep, "metric": q["metric"],
               "ts": datetime.now().isoformat(timespec="seconds"), "sql": None, "value": None,
               "status": None, "correct": False, "error": None}
        try:
            resp = client.chat(SYSTEM_PROMPT, user, {"run_key": run_key, "cond": cond, "gold_sql": q["gold_sql"]})
            rec.update({"system_merged": resp.get("system_merged", False), "latency_s": resp["latency_s"],
                        "finish_reason": resp.get("finish_reason"), "reasoning_chars": resp.get("reasoning_chars"),
                        "prompt_tokens": resp["prompt_tokens"], "completion_tokens": resp["completion_tokens"],
                        "raw_text": resp["text"][:2000]})
            sql = extract_sql(resp["text"])
            rec["sql"] = sql
            block = safety_check(sql)
            if block:
                rec["status"] = f"blocked:{block}"
            else:
                try:
                    rec["value"], rec["status"] = execute_readonly(sql)
                except sqlite3.Error as e:
                    rec["status"], rec["error"] = "sql_error", str(e)[:200]
            rec["correct"] = is_match(q["metric"], rec["value"], q["gold_value"])
        except Exception as e:  # 호출 실패는 오답으로 기록하되 실험은 계속
            if type(e).__name__ == "LLMError" and "HTTP 401" in str(e):
                raise AuthAbort(str(e)) from None
            rec["status"], rec["error"] = "llm_error", str(e)[:200]
        with write_lock:  # 여러 스레드가 같은 파일에 쓰므로 한 줄씩 순서대로
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    workers = max(1, a.workers)
    print(f"동시 호출 수={workers}")
    n_done = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(do_one, todo):
                n_done += 1
                if n_done % 10 == 0 or n_done == len(todo):
                    print(f"  진행 {n_done}/{len(todo)}", flush=True)
    except AuthAbort as e:
        sys.exit(f"인증 실패: {e}")
    print("완료. 요약: python experiment/run_experiment.py --summarize", path)


if __name__ == "__main__":
    main()
