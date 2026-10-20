"""사전 v2 평가용 보류 질문 10개와 골드 답을 만든다 (실험 전에 동결).

- 이 질문들은 v1 결과를 본 뒤 만들었지만, 사전 v2를 만들 때 쓰지 않았고 v2 실행 결과도 아직 보지 못한 질문이다.
- 표적(targeted): v2가 고친 실패 지점을 건드리는 질문(환불률, 유효 주문 수)
- 대조(control): 이미 맞히던 지표나 사전을 바꾸지 않은 지표(매출, 객단가, 취소율). v2의 부작용(회귀) 확인용
- 골드는 gold.py 의 SQL로 만들고, 파이썬으로 따로 다시 계산해 서로 맞는지 확인한다.

실행: python experiment/holdout.py  -> experiment/questions_A_holdout.json
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gold import DB, OUT, gold_sql  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):  # 출력이 파일·테스트용 버퍼로 바뀐 상태에서 불러와도 죽지 않게
    sys.stdout.reconfigure(encoding="utf-8")
JAN, FEB, MAR = "2026-01", "2026-02", "2026-03"

# (id, 질문, 지표, 월, 구분, 노리는 지점)
HOLDOUT = [
    ("H01", "1월과 2월 매출을 합치면 얼마야?", "net_revenue", [JAN, FEB], "대조", "다중 월 합산, 다른 표현"),
    ("H02", "2월에 판매한 것 중 환불로 나간 비중은?", "refund_rate", [FEB], "표적", "환불 비중 별칭, 전액·부분 환불, 결제 후 취소"),
    ("H03", "2~3월 환불률 알려줘", "refund_rate", [FEB, MAR], "표적", "결제 완료 해석(status vs paid_at), 새 기간 조합"),
    ("H04", "1~2월 환불률은 몇 %야?", "refund_rate", [JAN, FEB], "표적", "다중 월 합산 비율"),
    ("H05", "2월에 주문 취소가 몇 퍼센트나 돼?", "cancel_rate", [FEB], "대조", "취소율 별칭 변형(사전 변경 없음)"),
    ("H06", "1~2월 취소율은?", "cancel_rate", [JAN, FEB], "대조", "다중 월 합산 비율(사전 변경 없음)"),
    ("H07", "2~3월에 주문 하나당 평균 얼마 팔렸어?", "aov", [FEB, MAR], "대조", "객단가 별칭 변형, 새 기간 조합"),
    ("H08", "2월 객단가는?", "aov", [FEB], "대조", "분자·분모 정의"),
    ("H09", "1월 주문 건수가 몇 건이야?", "paid_orders", [JAN], "표적", "수식어 없는 '주문 건수'"),
    ("H10", "3월에 주문 몇 건 들어왔어?", "paid_orders", [MAR], "표적", "수식어 없는 '주문 몇 건'"),
]


def independent_check(con, metric, months):
    """SQL이 아니라 파이썬으로 다시 계산한다(gold.py 의 SQL과 다른 경로)."""
    orders = con.execute("SELECT order_id, ordered_at, paid_at, status, items_ex_vat FROM orders").fetchall()
    refunds = {oid: (t, amt) for oid, t, amt in con.execute("SELECT order_id, refund_type, refund_amount FROM refunds")}
    paid = [o for o in orders if o[2] and o[2][:7] in months]
    ref = sum((o[4] if refunds[o[0]][0] == "FULL" else refunds[o[0]][1] * 100 // 110) for o in paid if o[0] in refunds)
    gross = sum(o[4] for o in paid)
    valid = [o for o in paid if o[3] == "PAID"]
    oc = [o for o in orders if o[3] in ("PAID", "CANCELLED") and o[1][:7] in months]
    canc = sum(1 for o in oc if o[3] == "CANCELLED")
    return {"net_revenue": gross - ref, "paid_orders": len(valid), "aov": round((gross - ref) / len(valid)),
            "refund_rate": round(100 * ref / gross, 2), "cancel_rate": round(100 * canc / len(oc), 2)}[metric]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = []
    for qid, text, metric, months, group, target in HOLDOUT:
        sql = gold_sql(metric, months)
        (value,) = con.execute(sql).fetchone()
        check = independent_check(con, metric, months)
        assert abs(float(value) - float(check)) < 0.011, (qid, value, check)  # 두 계산이 맞아야 함
        out.append({"id": qid, "question": text, "metric": metric, "months": months, "group": group,
                    "targets_conflict": target, "gold_sql": sql, "gold_value": value})
    ledger = dict(con.execute("SELECT period_month, net_revenue_ex_vat FROM ledger"))
    for q in out:
        if q["metric"] == "net_revenue":
            assert round(q["gold_value"]) == sum(ledger[m] for m in q["months"]), q["id"]
    # newline="\n": Windows에서도 항상 LF로 써서, 다시 생성해도 파일 바이트(동결 해시)가 바뀌지 않게 한다
    with open(OUT / "questions_A_holdout.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"{'ID':4s} {'구분':3s} {'지표':12s} {'골드':>14s}  질문")
    for q in out:
        print(f"{q['id']:4s} {q['group']:3s} {q['metric']:12s} {q['gold_value']:>14,.2f}  {q['question']}")
    g = [q["group"] for q in out]
    print(f"\n표적 {g.count('표적')}개, 대조 {g.count('대조')}개. 두 방식(SQL, 파이썬) 골드 일치 확인 통과")


if __name__ == "__main__":
    main()
