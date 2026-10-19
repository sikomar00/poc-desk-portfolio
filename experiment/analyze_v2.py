"""사전 v2 실험(holdout) 사후 분석 (탐색용). 판정은 evaluate_v2.py 가 사전 지정 규칙으로 이미 했다.

사용: python experiment/analyze_v2.py RUN1.jsonl RUN2.jsonl
합성 데이터 실험이므로 SQL 패턴을 출력해도 된다.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
Q = {q["id"]: q for q in json.loads((HERE / "questions_A_holdout.json").read_text(encoding="utf-8"))}


def load(path):
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    meta = rows[0]
    last = {}
    for r in rows:
        if r.get("type") == "run":
            last[r["run_key"]] = r
    return meta["model"].split("/")[-1], list(last.values())


def main():
    data = dict(load(p) for p in sys.argv[1:3])
    print("[질문별 정답 (5회 중)]  B1 -> B2")
    print(f"  {'ID':4s} {'구분':3s} {'지표':12s} " + " ".join(f"{m[:12]:>14s}" for m in data) + "   질문")
    for qid, q in Q.items():
        cells = []
        for m, runs in data.items():
            b1 = sum(r["correct"] for r in runs if r["qid"] == qid and r["cond"] == "B1")
            b2 = sum(r["correct"] for r in runs if r["qid"] == qid and r["cond"] == "B2")
            cells.append(f"{b1}/5 -> {b2}/5")
        print(f"  {qid:4s} {q['group']:3s} {q['metric']:12s} " + " ".join(f"{c:>14s}" for c in cells) + f"   {q['question'][:24]}")

    print("\n[취소율(H05, H06): 사전 문구는 v1과 v2가 같았음. 오답의 값과 SQL 패턴]")
    for m, runs in data.items():
        for cond in ("B1", "B2"):
            rs = [r for r in runs if r["metric"] == "cancel_rate" and r["cond"] == cond]
            wrong = [r for r in rs if not r["correct"]]
            vals = sorted(round(r["value"] / Q[r["qid"]]["gold_value"], 2) for r in wrong if r["value"] is not None)
            use_paid = sum(1 for r in rs if r["sql"] and re.search(r"paid_at", r["sql"]))
            use_ordered = sum(1 for r in rs if r["sql"] and re.search(r"ordered_at", r["sql"]))
            print(f"  {m[:12]:12s} {cond}: 정답 {len(rs) - len(wrong)}/{len(rs)}, 오답 값/골드 {vals}, "
                  f"SQL에 ordered_at {use_ordered}건, paid_at {use_paid}건")

    print("\n[환불률(H02~H04) B2 오답: 값/골드 비율]")
    for m, runs in data.items():
        rs = [r for r in runs if r["metric"] == "refund_rate" and r["cond"] == "B2" and not r["correct"]]
        print(f"  {m[:12]:12s} 오답 {len(rs)}건: {sorted(round(r['value'] / Q[r['qid']]['gold_value'], 2) for r in rs if r['value'] is not None)}"
              f" (값 없음 {sum(1 for r in rs if r['value'] is None)})")

    print("\n[유효 주문 수(H09, H10) B2 오답: 값/골드 비율]")
    for m, runs in data.items():
        rs = [r for r in runs if r["metric"] == "paid_orders" and r["cond"] == "B2" and not r["correct"]]
        print(f"  {m[:12]:12s} 오답 {len(rs)}건: {sorted(round(r['value'] / Q[r['qid']]['gold_value'], 2) for r in rs if r['value'] is not None)}")

    print("\n[B1 vs B2 평균 응답 시간(초), 종료 사유]")
    for m, runs in data.items():
        for cond in ("B1", "B2"):
            rs = [r for r in runs if r["cond"] == cond]
            print(f"  {m[:12]:12s} {cond}: {sum(r['latency_s'] for r in rs) / len(rs):.0f}초, "
                  f"{dict(Counter(r.get('finish_reason') for r in rs))}, 상태 {dict(Counter(r['status'] for r in rs))}")


if __name__ == "__main__":
    main()
