"""실험 질문 20개의 '골드 답'(정답)과 H3 오류 SQL 테스트 세트를 만든다.

골드 답은 사람이 확정한 KPI 사전 v1(kpi_dictionary_A.json)을 SQL로 옮긴 것이다.
LLM이 만든 SQL의 실행 결과를 이 값과 비교해 채점한다. (합성 데이터이므로 공개 가능)

실행: python experiment/gold.py   → questions_A.json, h3_cases.json 생성
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # 출력이 파일·테스트용 버퍼로 바뀐 상태에서 불러와도 죽지 않게
    sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "poc_desk_sample.db"
OUT = Path(__file__).parent

Q1 = ["2026-01", "2026-02", "2026-03"]


def _in(months):
    return ",".join(f"'{m}'" for m in months)


def gold_sql(metric, months):
    paid_m = f"strftime('%Y-%m', o.paid_at) IN ({_in(months)})"
    refund_ex = ("COALESCE(SUM(CASE r.refund_type WHEN 'FULL' THEN o.items_ex_vat "
                 "ELSE r.refund_amount * 100 / 110 END), 0)")
    revenue = (f"SELECT SUM(o.items_ex_vat) - {refund_ex} FROM orders o "
               f"LEFT JOIN refunds r USING (order_id) WHERE o.paid_at IS NOT NULL AND {paid_m}")
    valid_orders = (f"SELECT COUNT(*) FROM orders o WHERE o.status='PAID' "
                    f"AND o.paid_at IS NOT NULL AND {paid_m}")
    if metric == "net_revenue":
        return revenue
    if metric == "paid_orders":
        return valid_orders
    if metric == "aov":
        return f"SELECT ROUND(1.0 * ({revenue}) / ({valid_orders}))"
    if metric == "refund_rate":
        return (f"SELECT ROUND(100.0 * {refund_ex} / SUM(o.items_ex_vat), 2) FROM orders o "
                f"LEFT JOIN refunds r USING (order_id) WHERE o.paid_at IS NOT NULL AND {paid_m}")
    if metric == "cancel_rate":
        return (f"SELECT ROUND(100.0 * SUM(o.status='CANCELLED') / COUNT(*), 2) FROM orders o "
                f"WHERE o.status IN ('PAID','CANCELLED') "
                f"AND strftime('%Y-%m', o.ordered_at) IN ({_in(months)})")
    raise ValueError(metric)


# (id, 질문, 지표, 월, 노리는 정의 충돌)
QUESTIONS = [
    ("Q01", "2026년 1월 매출 알려줘", "net_revenue", ["2026-01"], "부가세·취소·환불 포함 여부"),
    ("Q02", "2월에 얼마 벌었어?", "net_revenue", ["2026-02"], "부가세·환불, 모호한 표현"),
    ("Q03", "3월 순매출은?", "net_revenue", ["2026-03"], "'순'의 의미(환불 차감)"),
    ("Q04", "1분기 매출 합계는?", "net_revenue", Q1, "다중 월 합산"),
    ("Q05", "1월 31일 밤에 주문하고 다음 날 결제된 건이 들어간 2월 매출은?", "net_revenue", ["2026-02"], "결제일 vs 주문일"),
    ("Q06", "1월 환불률은?", "refund_rate", ["2026-01"], "분모 정의, 부분환불"),
    ("Q07", "2월에 환불이 얼마나 나갔어? 비율로 알려줘", "refund_rate", ["2026-02"], "분자·분모, 모호한 표현"),
    ("Q08", "3월 환불 비율", "refund_rate", ["2026-03"], "결제 후 취소의 전액환불 포함"),
    ("Q09", "1분기 환불률은?", "refund_rate", Q1, "합계끼리 나누기 vs 월 평균"),
    ("Q10", "1월 취소율", "cancel_rate", ["2026-01"], "결제 전·후 취소, PENDING 제외"),
    ("Q11", "2월 주문 중 취소된 비율은?", "cancel_rate", ["2026-02"], "분모에 PENDING 포함 여부"),
    ("Q12", "3월 취소율은 몇 %야?", "cancel_rate", ["2026-03"], "주문일 기준(예외 지표)"),
    ("Q13", "1분기 취소율은?", "cancel_rate", Q1, "합계끼리 나누기"),
    ("Q14", "1월 객단가는?", "aov", ["2026-01"], "분자 매출 정의, 분모 주문 수 정의"),
    ("Q15", "2월 평균 주문 금액", "aov", ["2026-02"], "부가세·배송비 포함 여부"),
    ("Q16", "3월 객단가 알려줘", "aov", ["2026-03"], "취소 주문의 분모 포함 여부"),
    ("Q17", "1분기 객단가는?", "aov", Q1, "합계끼리 나누기"),
    ("Q18", "1월 결제 건수는?", "paid_orders", ["2026-01"], "취소 건 포함 여부"),
    ("Q19", "2월 주문 몇 건이야?", "paid_orders", ["2026-02"], "주문 vs 결제 vs 유효, PENDING"),
    ("Q20", "3월에 실제로 팔린 주문 수", "paid_orders", ["2026-03"], "'실제로'의 의미(취소 제외)"),
]


def load_variants():
    spec = importlib.util.spec_from_file_location("vd", ROOT / "data" / "verify_definitions.py")
    vd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vd)
    return vd.VARIANTS


def main():
    con = sqlite3.connect(DB)
    questions = []
    for qid, text, metric, months, target in QUESTIONS:
        sql = gold_sql(metric, months)
        (value,) = con.execute(sql).fetchone()
        questions.append({"id": qid, "question": text, "metric": metric, "months": months,
                          "targets_conflict": target, "gold_sql": sql, "gold_value": value})
    # 골드가 원장과 맞는지 자기 검증 (매출 지표)
    ledger = dict(con.execute("SELECT period_month, net_revenue_ex_vat FROM ledger"))
    for q in questions:
        if q["metric"] == "net_revenue":
            expect = sum(ledger[m] for m in q["months"])
            assert round(q["gold_value"]) == expect, (q["id"], q["gold_value"], expect)
    # newline="\n": Windows에서도 항상 LF로 써서, 다시 생성해도 파일 바이트(해시)가 바뀌지 않게 한다
    with open(OUT / "questions_A.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(questions, ensure_ascii=False, indent=2))

    # H3: 틀린 매출 SQL 10개 = 오답 정의 V0~V4 x 2개월. 공식(V5)은 정답이므로 제외한다.
    variants = load_variants()
    wrong = [(n, sql) for n, sql in variants.items() if not n.startswith("V5")]
    cases = []
    for name, sql in wrong:
        for month in ("2026-01", "2026-03"):
            wrapped = f"SELECT * FROM ({sql}) WHERE m = '{month}'"
            row = con.execute(wrapped).fetchone()
            claimed = round(row[1])
            cases.append({"id": f"H3-{len(cases) + 1:02d}", "wrong_definition": name, "month": month,
                          "sql": wrapped, "claimed_value": claimed, "ledger_value": ledger[month],
                          "should_flag": claimed != ledger[month]})
    with open(OUT / "h3_cases.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(cases, ensure_ascii=False, indent=2))

    print(f"{'ID':4s} {'지표':12s} {'골드 답':>16s}  질문")
    for q in questions:
        print(f"{q['id']:4s} {q['metric']:12s} {q['gold_value']:>16,.2f}  {q['question']}")
    print(f"\nH3 오류 SQL {len(cases)}개, 플래그 대상 {sum(c['should_flag'] for c in cases)}개")
    for c in cases:
        diff = c['claimed_value'] - c['ledger_value']
        print(f"{c['id']} {c['month']} 주장={c['claimed_value']:>12,d} 원장={c['ledger_value']:>12,d} 차이={diff:>+12,d}  {c['wrong_definition'][:30]}")


if __name__ == "__main__":
    main()
