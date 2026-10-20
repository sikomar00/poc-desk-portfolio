"""사례 B: 정의별 '월 지출' 계산 (로컬 전용).

- 입력: data/private/transaction.csv (원본, 수정 안 함)
- 중간: data/private/bank.db (거래처명은 해시로 치환한 비식별 DB)
- 출력(금액 포함): data/private/variants_amounts.txt  ← Git 제외
- 콘솔/공개용: 정의 v1 대비 '차이 비율'만 출력

실행: python data/compute_spending_variants.py
"""
import csv
import hashlib
import secrets
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # 출력이 파일·테스트용 버퍼로 바뀐 상태에서 불러와도 죽지 않게
    sys.stdout.reconfigure(encoding="utf-8")
PRIV = Path(__file__).parent / "private"
SRC = PRIV / "transaction.csv"
DB = PRIV / "bank.db"
FULL_MONTHS = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 10)]


def build_db():
    salt = secrets.token_hex(8)  # 실행마다 달라지는 소금: 해시로 거래처를 역추적하기 어렵게 함
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE tx (
        id INTEGER PRIMARY KEY, d TEXT, t TEXT, type TEXT, cat TEXT, subcat TEXT,
        merchant TEXT, amount INTEGER, pay TEXT, ts TEXT)""")
    with open(SRC, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), 1):
            h = "m_" + hashlib.sha256((salt + r["내용"]).encode()).hexdigest()[:8]
            con.execute("INSERT INTO tx VALUES (?,?,?,?,?,?,?,?,?,?)", (
                i, r["날짜"], r["시간"], r["타입"], r["대분류"], r["소분류"], h,
                int(float(r["금액"].replace(",", ""))), r["결제수단"], r["날짜"] + " " + r["시간"]))
    con.commit()
    return con


# 공통: 월 귀속 = 거래 날짜. W7만 05시 이전을 전날로 본다.
M = "substr(d,1,7)"
DEDUP = "(SELECT * FROM tx WHERE id IN (SELECT MIN(id) FROM tx GROUP BY d,t,amount,pay,merchant))"
V1_BODY = """
    SELECT {m} m, -SUM(amount) v FROM {src} WHERE type='지출' GROUP BY m
    UNION ALL
    SELECT {m} m, SUM(ABS(amount)) v FROM {src} WHERE cat='저축' AND type='이체' GROUP BY m
    UNION ALL
    SELECT {m} m, -SUM(amount) v FROM {src} WHERE cat='투자' AND type='이체' GROUP BY m"""

VARIANTS = {
    "W0 지출 타입의 절댓값 합 (양수=환불도 지출로 더함)":
        f"SELECT {M} m, SUM(ABS(amount)) FROM tx WHERE type='지출' GROUP BY m",
    "W1 지출 타입 합계 (양수는 환불로 차감, 이체 전부 제외)":
        f"SELECT {M} m, -SUM(amount) FROM tx WHERE type='지출' GROUP BY m",
    "W2 W1 + 이체 중 나간 돈 전부 (내계좌이체 포함)":
        f"""SELECT m, SUM(v) FROM (
              SELECT {M} m, -SUM(amount) v FROM tx WHERE type='지출' GROUP BY m
              UNION ALL SELECT {M} m, SUM(-amount) v FROM tx WHERE type='이체' AND amount<0 GROUP BY m
            ) GROUP BY m""",
    "W3 W1 + 내계좌이체를 제외한 이체 중 나간 돈":
        f"""SELECT m, SUM(v) FROM (
              SELECT {M} m, -SUM(amount) v FROM tx WHERE type='지출' GROUP BY m
              UNION ALL SELECT {M} m, SUM(-amount) v FROM tx
                WHERE type='이체' AND cat!='내계좌이체' AND amount<0 GROUP BY m
            ) GROUP BY m""",
    "W4 공식 v1 (W1 + 저축 절댓값 + 투자 순투입)":
        "SELECT m, SUM(v) FROM (" + V1_BODY.format(m=M, src="tx") + ") GROUP BY m",
    "W5 v1에서 같은 행 중복 25건 제거":
        "SELECT m, SUM(v) FROM (" + V1_BODY.format(m=M, src=DEDUP) + ") GROUP BY m",
    "W6 v1에서 포인트 결제 제외":
        "SELECT m, SUM(v) FROM (" + V1_BODY.format(m=M, src="(SELECT * FROM tx WHERE pay NOT LIKE '%포인트%')") + ") GROUP BY m",
    "W7 v1이되 05시 이전 거래는 전날로 귀속":
        "SELECT m, SUM(v) FROM (" + V1_BODY.format(
            m="substr(date(ts, CASE WHEN t < '05:00' THEN '-1 day' ELSE '0 day' END),1,7)", src="tx") + ") GROUP BY m",
}
REF = "W4 공식 v1 (W1 + 저축 절댓값 + 투자 순투입)"


def main():
    con = build_db()
    res = {n: dict(con.execute(sql).fetchall()) for n, sql in VARIANTS.items()}
    ref = res[REF]
    months = sorted(set().union(*[set(r) for r in res.values()]))
    with open(PRIV / "variants_amounts.txt", "w", encoding="utf-8") as f:  # 금액 포함: 비공개
        f.write("정의별 월 지출(원), 비공개\n")
        f.write(f"{'정의':60s}" + "".join(f"{m:>12s}" for m in months) + "\n")
        for n, r in res.items():
            f.write(f"{n:60s}" + "".join(f"{int(r.get(m, 0)):>12,d}" for m in months) + "\n")
    print("[v1 대비 차이 비율] 완전한 11개월(2025-11~2026-09) 기준, 금액은 출력하지 않음")
    print("| 정의 | 11개월 합계 차이 | 월별 최소 | 월별 최대 |")
    print("|---|---:|---:|---:|")
    for n, r in res.items():
        tot = sum(r.get(m, 0) for m in FULL_MONTHS)
        base = sum(ref.get(m, 0) for m in FULL_MONTHS)
        ratios = [(r.get(m, 0) / ref[m] - 1) * 100 for m in FULL_MONTHS if ref.get(m)]
        print(f"| {n} | {(tot / base - 1) * 100:+.1f}% | {min(ratios):+.1f}% | {max(ratios):+.1f}% |")
    nrows = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
    print(f"\n적재 행 수: {nrows} (비식별 DB: data/private/bank.db)")


if __name__ == "__main__":
    main()
