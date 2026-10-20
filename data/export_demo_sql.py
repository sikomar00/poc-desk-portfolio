"""합성 주문 DB(SQLite)를 Supabase `demo` 스키마용 SQL 파일로 내보낸다.

- 값은 한 글자도 바꾸지 않는다(끝난 실험의 동결 해시·골드 답이 이 데이터에 묶여 있다).
- 실행해도 몇 번이든 같은 결과가 나오게 먼저 비우고(TRUNCATE) 하나의 트랜잭션 안에서 넣는다.
- 원장(ledger)은 `app.ledger`로 가는 별도 작업이라 여기서 내보내지 않는다.

실행: python data/export_demo_sql.py <출력 .sql 경로>
적용: npx supabase db query --linked -f <출력 .sql 경로>
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "poc_desk_sample.db"
# 외래키 순서(부모 → 자식)
TABLES = [
    ("customers", ["customer_id", "name", "joined_at"]),
    ("products", ["product_id", "name", "category", "unit_price_ex_vat"]),
    ("orders", ["order_id", "customer_id", "ordered_at", "paid_at", "status",
                "items_ex_vat", "vat_amount", "shipping_fee", "total_amount"]),
    ("order_items", ["item_id", "order_id", "product_id", "quantity", "unit_price_ex_vat"]),
    ("refunds", ["refund_id", "order_id", "refunded_at", "refund_type", "refund_amount"]),
]


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def main(out_path):
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    lines = ["begin;",
             "truncate demo.refunds, demo.order_items, demo.orders, demo.products, demo.customers;"]
    for table, cols in TABLES:
        rows = con.execute(f"select {', '.join(cols)} from {table} order by 1").fetchall()
        for i in range(0, len(rows), 200):
            chunk = rows[i:i + 200]
            values = ",\n".join("(" + ", ".join(lit(v) for v in r) + ")" for r in chunk)
            lines.append(f"insert into demo.{table} ({', '.join(cols)}) values\n{values};")
        print(f"{table:12s} {len(rows):>5d} rows")
    lines.append("commit;")
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("written:", out_path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
