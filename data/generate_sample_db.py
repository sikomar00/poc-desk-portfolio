"""합성 주문 데이터 생성기 (realDefinition 샘플 DB).

- 모든 데이터는 가짜(합성)이며 seed 고정으로 항상 같은 결과가 나온다.
- 지표 정의 충돌 4종(부가세, 취소, 부분 환불, 결제일 vs 주문일)을 의도적으로 심는다.
- `ledger`(원장)는 SQL이 아니라 파이썬으로 따로 계산해 저장한다.
  나중에 SQL 결과를 이 값과 맞춰 보는 것이 '대사(reconciliation)'이다.

공식 매출 정의(KPI 사전 v1):
  매출 = 결제 완료 주문의 상품금액(부가세 제외, 배송비 제외)
         - 환불액(부가세 제외),  기간 기준 = 결제일(paid_at)
  (결제 후 취소는 전액 환불로 기록되어 있으므로 환불로만 차감한다 = 이중 차감 금지)
  환불은 '원래 결제월' 매출에서 차감한다 (환불일 월이 아님)
  전액환불은 상품금액(부가세·배송비 제외) 전부, 부분환불은 환불액을 1.1로 나눠 부가세 제외

실행: python data/generate_sample_db.py
"""
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SEED = 42
VAT_RATE = 10  # 부가세 10% (정수 계산을 위해 퍼센트 정수로 사용)
DB_PATH = Path(__file__).parent / "poc_desk_sample.db"
START = datetime(2026, 1, 1)
END = datetime(2026, 3, 31, 23, 59, 59)

PRODUCTS = [
    (1, "무선 마우스", "전자", 20000),
    (2, "기계식 키보드", "전자", 80000),
    (3, "USB-C 케이블", "전자", 10000),
    (4, "텀블러", "생활", 15000),
    (5, "노트 3권 세트", "문구", 9000),
    (6, "백팩", "패션", 60000),
    (7, "러닝화", "패션", 90000),
    (8, "머그컵", "생활", 8000),
]

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    joined_at   TEXT NOT NULL
);
CREATE TABLE products (
    product_id     INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    unit_price_ex_vat INTEGER NOT NULL
);
CREATE TABLE orders (
    order_id     INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(customer_id),
    ordered_at   TEXT NOT NULL,            -- 주문일시
    paid_at      TEXT,                     -- 결제일시 (결제 전이면 NULL)
    status       TEXT NOT NULL CHECK (status IN ('PAID','CANCELLED','PENDING')),
    items_ex_vat INTEGER NOT NULL,         -- 상품금액 합계(부가세 제외)
    vat_amount   INTEGER NOT NULL,         -- 부가세
    shipping_fee INTEGER NOT NULL,         -- 배송비 (매출 제외 대상)
    total_amount INTEGER NOT NULL          -- 고객이 낸 총액 = 상품금액 + 부가세 + 배송비
);
CREATE TABLE order_items (
    item_id    INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity   INTEGER NOT NULL,
    unit_price_ex_vat INTEGER NOT NULL
);
CREATE TABLE refunds (
    refund_id    INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL REFERENCES orders(order_id),
    refunded_at  TEXT NOT NULL,
    refund_type  TEXT NOT NULL CHECK (refund_type IN ('FULL','PARTIAL')),
    refund_amount INTEGER NOT NULL         -- 고객에게 돌려준 금액(부가세 포함, 전액환불은 배송비도 포함)
);
CREATE TABLE ledger (
    period_month TEXT PRIMARY KEY,         -- 'YYYY-MM' (결제일 기준 월)
    net_revenue_ex_vat INTEGER NOT NULL,   -- 공식 정의(KPI 사전 v1)로 따로 계산한 값
    definition_version TEXT NOT NULL
);
"""

CUSTOMER_NAMES = ["김하늘", "이도윤", "박서연", "최민준", "정지우", "강예린", "조현우", "윤소희",
                  "장태영", "임나연", "한지민", "오세훈", "서유진", "신재원", "권다은"]


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def month_of(dt):
    return dt.strftime("%Y-%m")


def make_orders(rng):
    """주문 시각 후보를 만든다. 월말 경계 주문을 일부러 끼워 넣는다."""
    times = []
    total = (END - START).total_seconds()
    for _ in range(480):
        times.append(START + timedelta(seconds=rng.uniform(0, total)))
    # 경계 주문: 월 마지막 날 늦은 밤에 주문 → 결제는 다음 달 (정의 충돌 ④)
    for last_day in [datetime(2026, 1, 31), datetime(2026, 2, 28)]:
        for _ in range(10):
            times.append(last_day.replace(hour=rng.choice([22, 23]), minute=rng.randint(0, 59),
                                          second=rng.randint(0, 59)))
    return sorted(times)


def main():
    rng = random.Random(SEED)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)

    con.executemany("INSERT INTO products VALUES (?,?,?,?)", PRODUCTS)
    customers = [(i + 1, n, "2025-%02d-15" % rng.randint(1, 12)) for i, n in enumerate(CUSTOMER_NAMES)]
    con.executemany("INSERT INTO customers VALUES (?,?,?)", customers)

    ledger = {}  # month -> 공식 매출 (파이썬으로 독립 계산)
    item_id = 1
    refund_id = 1
    order_rows, item_rows, refund_rows = [], [], []

    for order_id, ordered in enumerate(make_orders(rng), start=1):
        cust = rng.randint(1, len(customers))
        # 상품 1~3종
        lines = []
        for p in rng.sample(PRODUCTS, rng.randint(1, 3)):
            qty = rng.randint(1, 3)
            lines.append((p[0], qty, p[3]))
        items_ex_vat = sum(q * price for _, q, price in lines)
        vat = items_ex_vat * VAT_RATE // 100
        shipping = 0 if items_ex_vat >= 50000 else 3000  # 배송비는 매출 제외 대상 (정의 충돌 ①의 일부)
        total = items_ex_vat + vat + shipping

        # 알려진 한계(수정하지 않음): 조건이 '1월 28일 밤'도 월말 경계로 취급한다(진짜 월말은 1/31, 2/28). 이 주문들은 PENDING·CANCELLED가
        # 되지 못하고 항상 PAID가 되며, 월을 넘기지도 않는다. 고치면 DB의 모든 값(원장, 골드 답, 실험 데이터)이 바뀌어
        # 이미 끝난 실험 결과(docs/07, 09)와 동결 해시가 무효가 되므로 그대로 둔다. 새 데이터 버전을 만들 때 고칠 것.
        is_boundary = ordered.day in (31, 28) and ordered.hour >= 22 and ordered.month in (1, 2)
        roll = rng.random()
        paid_at = None
        status = "PAID"
        if roll < 0.06 and not is_boundary:
            status = "PENDING"          # 결제 전, 아직 매출 아님
        elif roll < 0.14 and not is_boundary:
            status = "CANCELLED"        # 결제 전 취소(paid_at NULL) 또는 결제 후 취소
        if status != "PENDING":
            if is_boundary:
                paid_at = ordered + timedelta(hours=rng.randint(2, 6))  # 다음 달로 넘어감
            elif status == "CANCELLED" and rng.random() < 0.5:
                paid_at = None          # 결제 전 취소
            else:
                paid_at = ordered + timedelta(minutes=rng.randint(1, 180))
        # 결제 시각이 분석 기간을 넘으면 결제 전으로 취급하지 않고 그대로 기록하되 원장은 3월까지만 계산
        for pid, q, price in lines:
            item_rows.append((item_id, order_id, pid, q, price))
            item_id += 1
        order_rows.append((order_id, cust, fmt(ordered), fmt(paid_at) if paid_at else None,
                           status, items_ex_vat, vat, shipping, total))

        revenue_ex_vat = 0
        refunded_ex_vat = 0
        if paid_at is not None:
            revenue_ex_vat = items_ex_vat
            if status == "CANCELLED":
                # 결제 후 취소: 전액 환불로 기록 (취소와 환불이 겹치는 정의 충돌 ②, ③)
                ref_dt = paid_at + timedelta(days=rng.randint(0, 3), hours=rng.randint(1, 20))
                refund_rows.append((refund_id, order_id, fmt(ref_dt), 'FULL', total))
                refund_id += 1
                refunded_ex_vat = items_ex_vat
            elif rng.random() < 0.10:
                # 부분 환불: 상품금액의 일부(부가세 포함 금액으로 환불)
                part_ex = max(1000, (items_ex_vat // 3) // 1000 * 1000)
                ref_dt = paid_at + timedelta(days=rng.randint(1, 20))
                refund_rows.append((refund_id, order_id, fmt(ref_dt), 'PARTIAL', part_ex * (100 + VAT_RATE) // 100))
                refund_id += 1
                refunded_ex_vat = part_ex
            # 원장 반영: 매출은 결제월, 환불은 환불월에 차감하지 않고 '결제월'에 귀속
            # (KPI 사전 v1: 환불은 원 결제월 매출에서 차감) — 정의를 하나로 고정
            m = month_of(paid_at)
            if m <= "2026-03":
                ledger[m] = ledger.get(m, 0) + revenue_ex_vat - refunded_ex_vat

    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", order_rows)
    con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", item_rows)
    con.executemany("INSERT INTO refunds VALUES (?,?,?,?,?)", refund_rows)
    con.executemany("INSERT INTO ledger VALUES (?,?,?)",
                    [(m, v, "v1") for m, v in sorted(ledger.items())])
    con.commit()
    for t in ["customers", "products", "orders", "order_items", "refunds", "ledger"]:
        print(f"{t:12s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>5d} rows")
    con.close()
    print("created:", DB_PATH)


if __name__ == "__main__":
    main()
