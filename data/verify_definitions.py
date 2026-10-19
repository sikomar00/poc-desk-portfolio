"""'매출'을 정의별로 계산해 보고, 공식 정의가 원장과 일치하는지 확인한다.

실행: python data/verify_definitions.py
출력은 docs/05-data-design.md 의 '정의별 숫자' 표에 옮겨 적는다.
"""
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
DB = Path(__file__).parent / "poc_desk_sample.db"

# 각 변형은 '한 가지 정의 선택'만 다르다. month 는 '2026-03' 형식.
VARIANTS = {
    "V0 주문총액 합계 (모든 주문, 주문일)": """
        SELECT strftime('%Y-%m', ordered_at) m, SUM(total_amount)
        FROM orders GROUP BY m""",
    "V1 결제완료 총액 (부가세·배송비 포함, 결제일)": """
        SELECT strftime('%Y-%m', paid_at) m, SUM(total_amount)
        FROM orders WHERE paid_at IS NOT NULL GROUP BY m""",
    "V2 결제완료 상품금액 (부가세·배송비 제외, 결제일, 취소·환불 미반영)": """
        SELECT strftime('%Y-%m', paid_at) m, SUM(items_ex_vat)
        FROM orders WHERE paid_at IS NOT NULL GROUP BY m""",
    "V3 V2에서 status=CANCELLED 제외 (환불은 미반영)": """
        SELECT strftime('%Y-%m', paid_at) m, SUM(items_ex_vat)
        FROM orders WHERE paid_at IS NOT NULL AND status='PAID' GROUP BY m""",
    "V4 V3 + 환불 차감 (환불일 월에 귀속, 환불액은 부가세 포함 그대로)": """
        SELECT m, SUM(v) FROM (
            SELECT strftime('%Y-%m', paid_at) m, items_ex_vat v
              FROM orders WHERE paid_at IS NOT NULL AND status='PAID'
            UNION ALL
            SELECT strftime('%Y-%m', refunded_at) m, -refund_amount
              FROM refunds r JOIN orders o USING(order_id) WHERE o.status='PAID'
        ) GROUP BY m""",
    "V5 공식: 결제일, 부가세·배송비 제외, 환불은 원 결제월에서 차감": """
        SELECT strftime('%Y-%m', o.paid_at) m,
               SUM(o.items_ex_vat) - COALESCE(SUM(
                   CASE r.refund_type WHEN 'FULL' THEN o.items_ex_vat
                                      ELSE r.refund_amount * 100 / 110 END), 0)
        FROM orders o LEFT JOIN refunds r USING(order_id)
        WHERE o.paid_at IS NOT NULL GROUP BY m""",
}
MONTHS = ["2026-01", "2026-02", "2026-03"]


def main():
    con = sqlite3.connect(DB)
    ledger = dict(con.execute("SELECT period_month, net_revenue_ex_vat FROM ledger"))
    print(f"{'정의':70s}" + "".join(f"{m:>14s}" for m in MONTHS))
    print(f"{'원장(정답)':70s}" + "".join(f"{ledger.get(m, 0):>14,d}" for m in MONTHS))
    for name, sql in VARIANTS.items():
        got = dict(con.execute(sql).fetchall())
        print(f"{name:70s}" + "".join(f"{int(got.get(m, 0)):>14,d}" for m in MONTHS))
    # V5(공식)는 원장과 같아야 한다 (3월까지)
    v5 = dict(con.execute(VARIANTS["V5 공식: 결제일, 부가세·배송비 제외, 환불은 원 결제월에서 차감"]).fetchall())
    ok = all(int(v5.get(m, 0)) == ledger.get(m, 0) for m in MONTHS)
    print("\n공식 SQL(V5) == 원장:", "PASS" if ok else "FAIL")

    # 경계 주문: 주문월 != 결제월
    n = con.execute("""SELECT COUNT(*) FROM orders WHERE paid_at IS NOT NULL
        AND strftime('%Y-%m', ordered_at) != strftime('%Y-%m', paid_at)""").fetchone()[0]
    print("주문월≠결제월 주문 수:", n)
    # 이중 차감 함정: 결제 후 취소 주문 수, 부분 환불 수
    print("결제 후 취소 주문 수:", con.execute(
        "SELECT COUNT(*) FROM orders WHERE status='CANCELLED' AND paid_at IS NOT NULL").fetchone()[0])
    print("결제 전 취소 주문 수:", con.execute(
        "SELECT COUNT(*) FROM orders WHERE status='CANCELLED' AND paid_at IS NULL").fetchone()[0])
    print("부분 환불 건수:", con.execute(
        "SELECT COUNT(*) FROM refunds WHERE refund_type='PARTIAL'").fetchone()[0])
    print("결제월≠환불월 환불 건수:", con.execute("""SELECT COUNT(*) FROM refunds r JOIN orders o USING(order_id)
        WHERE strftime('%Y-%m', r.refunded_at) != strftime('%Y-%m', o.paid_at)""").fetchone()[0])
    print("PENDING 주문 수:", con.execute("SELECT COUNT(*) FROM orders WHERE status='PENDING'").fetchone()[0])
    con.close()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
