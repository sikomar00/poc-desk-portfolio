"""실험 기록(jsonl) 사후 분석 (탐색용). 판정은 run_experiment.py --summarize 가 사전 고정 기준으로 한다.

사용: python experiment/analyze_run.py experiment/runs/XXXX.jsonl            # 질문별 정확도, 오류, 값 패턴
      python experiment/analyze_run.py experiment/runs/XXXX.jsonl --show Q06 # 질문 하나의 조건 B 생성 SQL 2개 보기
합성 데이터 실험이므로 SQL을 그대로 출력해도 된다. (사례 B 실데이터에는 쓰지 말 것)
"""
import collections
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent


def main():
    path = sys.argv[1]
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    runs = [r for r in rows if r.get("type") == "run"]
    Q = {q["id"]: q for q in json.loads((HERE / "questions_A.json").read_text(encoding="utf-8"))}

    if "--show" in sys.argv:
        qid = sys.argv[sys.argv.index("--show") + 1]
        print(f"=== {qid} {Q[qid]['question']}  골드={Q[qid]['gold_value']}")
        for r in [r for r in runs if r["qid"] == qid and r["cond"] == "B"][:2]:
            print(f"-- 값={r['value']} 정답={r['correct']}\n{r['sql']}\n")
        return

    print("종료 사유:", dict(collections.Counter(r.get("finish_reason") for r in runs)))
    for c in "AB":
        rs = [r for r in runs if r["cond"] == c]
        print(f"조건 {c} 평균 응답 시간: {sum(r['latency_s'] for r in rs) / len(rs):.0f}초")
    print("\n[B 조건 SQL 오류]")
    for r in runs:
        if r["cond"] == "B" and r["status"] in ("sql_error", "llm_error"):
            print(" ", r["qid"], r["status"], (r["error"] or "")[:70])
    print("\n[질문별 정확도 (5회 중)]")
    for qid in Q:
        a = sum(r["correct"] for r in runs if r["qid"] == qid and r["cond"] == "A")
        b = sum(r["correct"] for r in runs if r["qid"] == qid and r["cond"] == "B")
        print(f"  {qid} {Q[qid]['metric']:11s} A {a}/5  B {b}/5  | {Q[qid]['question'][:30]}")
    print("\n[B 오답의 값 / 골드 비율 (작은 순)]")
    for metric in ("net_revenue", "paid_orders", "aov", "refund_rate", "cancel_rate"):
        rat = []
        for r in runs:
            g = Q[r["qid"]]["gold_value"]
            if r["cond"] == "B" and r["metric"] == metric and not r["correct"] and r["value"] is not None and g:
                rat.append(round(r["value"] / g, 3))
        print(f"  {metric}: {sorted(rat)[:10]} (n={len(rat)})")


if __name__ == "__main__":
    main()
