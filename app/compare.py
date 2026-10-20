"""정의 비교: 같은 '매출' 질문을 정의별로 계산해 나란히 보여 준다.

이 프로젝트가 풀려는 문제("정의가 다르면 같은 질문도 다른 숫자가 된다")를 화면에서 바로 보이게 하는 기능이다.
정의 6가지(V0~V5)는 실험·문서에서 쓴 것(data/verify_definitions.py)을 그대로 쓰고, 새 계산식을 만들지 않는다.
Supabase(PostgreSQL)에서는 같은 SQL의 날짜 함수만 바꿔 실행한다.
"""
import re

from gold import load_variants  # experiment/gold.py → data/verify_definitions.py 의 VARIANTS

OFFICIAL = "V5"

# 정의마다 무엇이 다른지 한눈에 보이게, 네 가지 선택(어떤 주문·금액·취소/환불·기준일)로 풀어 쓴다 (docs/05)
META = {
    "V0": {"title": "주문 총액 합계", "orders": "모든 주문(취소·결제 전 포함)", "amount": "부가세·배송비 포함", "refund": "반영 안 함", "date": "주문일"},
    "V1": {"title": "결제 완료 총액", "orders": "결제 완료 주문", "amount": "부가세·배송비 포함", "refund": "반영 안 함", "date": "결제일"},
    "V2": {"title": "결제 완료 상품금액", "orders": "결제 완료 주문", "amount": "상품금액만(부가세·배송비 제외)", "refund": "반영 안 함", "date": "결제일"},
    "V3": {"title": "취소 주문 제외", "orders": "결제 완료 중 취소 제외", "amount": "상품금액만", "refund": "환불 미반영", "date": "결제일"},
    "V4": {"title": "환불 차감(환불일 기준)", "orders": "결제 완료 중 취소 제외", "amount": "상품금액만", "refund": "환불일 월에서 차감, 부가세 포함액 그대로", "date": "결제일"},
    "V5": {"title": "공식 정의", "orders": "결제 완료 주문(결제 후 취소는 환불로 차감)", "amount": "상품금액만", "refund": "원래 결제월에서 차감, 부가세 제외", "date": "결제일"},
}

_STRFTIME = re.compile(r"strftime\('%Y-%m',\s*([A-Za-z_][A-Za-z0-9_.]*)\)")


def to_postgres(sql):
    """SQLite 날짜 함수 strftime('%Y-%m', 열)을 PostgreSQL to_char(열, 'YYYY-MM')으로 바꾼다. 다른 부분은 그대로다."""
    return _STRFTIME.sub(lambda m: f"to_char({m.group(1)}, 'YYYY-MM')", sql)


def compare(months, run_rows, postgres=False):
    """선택한 달(months)을 정의별로 계산한다. run_rows(sql) 은 [(월, 값), ...] 을 돌려주는 함수(읽기 전용)."""
    rows = []
    for name, sql in load_variants().items():
        key = name.split()[0]
        got = {}
        for m, v in run_rows(to_postgres(sql) if postgres else sql):
            if m is not None and v is not None:
                got[m] = float(v)
        by_month = {m: got.get(m, 0.0) for m in months}
        rows.append({"key": key, "label": name, **META[key], "official": key == OFFICIAL,
                     "by_month": by_month, "total": sum(by_month.values())})
    base = next(r for r in rows if r["official"])["total"]
    for r in rows:
        r["diff"] = r["total"] - base
        r["diff_pct"] = (r["diff"] / base * 100) if base else None
    totals = [r["total"] for r in rows]
    return {"months": list(months), "rows": rows, "official_total": base,
            "spread": max(totals) - min(totals), "spread_pct": ((max(totals) - min(totals)) / base * 100) if base else None}
