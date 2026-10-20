"""H3 검증: 원장 대사(reconciliation)가 틀린 매출 SQL을 걸러내는가? (LLM 없이 실행 가능)

대사 규칙(PoC Desk F6의 시제품):  |SQL 결과 - 원장| 이 허용 오차 이내면 PASS, 아니면 FLAG.

3단계로 확인한다.
  1) 틀린 SQL 10개(h3_cases.json)를 실행해 FLAG 되는지, 허용 오차를 키우면 놓치는지
  2) 대조군: 공식(정답) SQL은 PASS 해야 한다 (거짓 경보 확인)
  3) 소급 분석: 실제 LLM 실행 기록(experiment/runs/*.jsonl)의 매출 질문(Q01~Q05) 결과를 같은 규칙으로 대사

주의: 대사는 독립적인 정답 출처가 있는 지표(여기서는 매출의 원장)에만 쓸 수 있다.

실행: python experiment/h3_reconcile.py
"""
import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # 출력이 파일·테스트용 버퍼로 바뀐 상태에서 불러와도 죽지 않게
    sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
DB = HERE.parent / "data" / "poc_desk_sample.db"
TOLERANCES = [0.0, 0.001, 0.005, 0.01, 0.02, 0.05]  # 상대 허용 오차: 0%, 0.1%, 0.5%, 1%, 2%, 5%
REVENUE_QUESTIONS = {"Q01": ["2026-01"], "Q02": ["2026-02"], "Q03": ["2026-03"],
                     "Q04": ["2026-01", "2026-02", "2026-03"], "Q05": ["2026-02"]}


def reconcile(value, ledger_value, rel_tol=0.0):
    """허용 오차 안이면 PASS. 정수 금액이므로 0.5원 미만 차이는 같은 값으로 본다."""
    if value is None:
        return "FLAG"
    diff = abs(value - ledger_value)
    return "PASS" if diff < 0.5 or diff <= rel_tol * ledger_value else "FLAG"


def label(t):
    return "정확히 일치(0%)" if t == 0 else f"±{t * 100:g}%"


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ledger = dict(con.execute("SELECT period_month, net_revenue_ex_vat FROM ledger"))

    # 1) 틀린 SQL 10개
    cases = json.loads((HERE / "h3_cases.json").read_text(encoding="utf-8"))
    print("[1] 틀린 SQL 10개를 대사한 결과")
    values = []
    for c in cases:
        v = round(con.execute(c["sql"]).fetchone()[1])
        assert v == c["claimed_value"], (c["id"], v, c["claimed_value"])  # 저장된 값과 재실행 값이 같아야 함
        values.append((c, v, ledger[c["month"]]))
    print(f"{'허용 오차':16s} {'검출':>6s} {'놓침':>6s}  놓친 사례(오차율)")
    for t in TOLERANCES:
        missed = [(c["id"], (v - l) / l * 100) for c, v, l in values if reconcile(v, l, t) == "PASS"]
        print(f"{label(t):16s} {len(values) - len(missed):>4d}/10 {len(missed):>4d}   "
              + ", ".join(f"{i}({e:+.2f}%)" for i, e in missed))
    print("\n  개별 오차율:")
    for c, v, l in values:
        print(f"    {c['id']} {c['month']} {(v - l) / l * 100:+7.2f}%  {c['wrong_definition'][:34]}")

    # 2) 대조군: 공식 정의(V5)는 PASS 해야 한다
    sys.path.insert(0, str(HERE.parent / "data"))
    import verify_definitions as vd
    official = dict(con.execute(vd.VARIANTS["V5 공식: 결제일, 부가세·배송비 제외, 환불은 원 결제월에서 차감"]).fetchall())
    ctrl = [reconcile(round(official[m]), ledger[m], 0.0) for m in ("2026-01", "2026-02", "2026-03")]
    print(f"\n[2] 대조군(공식 SQL, 3개월): {ctrl.count('PASS')}/3 PASS (거짓 경보 {ctrl.count('FLAG')}건)")

    # 3) 실제 LLM 실행 기록 소급 대사
    print("\n[3] 실제 LLM 실행 기록(매출 질문 Q01~Q05)을 같은 규칙으로 대사")
    runs = []
    for f in sorted((HERE / "runs").glob("*.jsonl")):
        rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        meta = next(r for r in rows if r.get("type") == "meta")
        if meta.get("suite", "main") != "main":  # 보류 질문(holdout) 기록은 매출 질문 Q01~Q05가 없으므로 제외
            continue
        for r in rows:
            if r.get("type") == "run" and r["qid"] in REVENUE_QUESTIONS and r["cond"] == "B":
                r["_model"] = meta["model"]
                runs.append(r)
    for r in runs:
        r["_ledger"] = sum(ledger[m] for m in REVENUE_QUESTIONS[r["qid"]])
    wrong = [r for r in runs if not r["correct"]]
    right = [r for r in runs if r["correct"]]
    print(f"  조건 B 매출 호출 {len(runs)}회 중 정답 {len(right)}, 오답 {len(wrong)}")
    print(f"  {'허용 오차':16s} {'오답 검출':>10s} {'정답 거짓경보':>14s}")
    for t in TOLERANCES:
        det = sum(reconcile(r["value"], r["_ledger"], t) == "FLAG" for r in wrong)
        fa = sum(reconcile(r["value"], r["_ledger"], t) == "FLAG" for r in right)
        print(f"  {label(t):16s} {det:>5d}/{len(wrong):<4d} {fa:>9d}/{len(right):<4d}")
    errs = sorted(abs(r["value"] - r["_ledger"]) / r["_ledger"] * 100 for r in wrong if r["value"] is not None)
    print(f"  오답의 상대 오차(%) 오름차순: {[round(e, 2) for e in errs]}")
    none_cnt = sum(1 for r in wrong if r["value"] is None)
    print(f"  (값이 없는 오답 {none_cnt}건은 FLAG 처리)")


if __name__ == "__main__":
    main()
