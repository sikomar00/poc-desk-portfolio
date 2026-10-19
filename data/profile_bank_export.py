"""뱅크샐러드 내보내기 CSV 구조 점검 (집계만 출력, 개별 거래 행·내용·이름은 출력하지 않는다).

실행: python data/profile_bank_export.py data/private/transaction.csv
"""
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def read_rows(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit("인코딩을 알 수 없음")
    first = text.splitlines()[0]
    delim = "\t" if first.count("\t") >= first.count(",") else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
    return enc, delim, rows


def main(path):
    enc, delim, rows = read_rows(path)
    cols = list(rows[0].keys())
    print(f"인코딩={enc}  구분자={'TAB' if delim == chr(9) else delim!r}  행 수={len(rows):,}")
    print("열:", cols)
    dates = sorted(r["날짜"] for r in rows)
    print("날짜 범위:", dates[0], "~", dates[-1])
    print("\n[열별 빈 값 수]")
    for c in cols:
        print(f"  {c}: {sum(1 for r in rows if not (r[c] or '').strip())}")
    for c in ("타입", "대분류", "화폐", "결제수단"):
        print(f"\n[{c} 분포]")
        for k, v in Counter(r[c] for r in rows).most_common(15):
            print(f"  {k or '(빈 값)'}: {v}")
    amt = lambda r: int(float(r["금액"].replace(",", "")))
    print("\n[월별 × 타입 건수 / 금액 합계]")
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        k = (r["날짜"][:7], r["타입"])
        agg[k][0] += 1
        agg[k][1] += amt(r)
    for (m, t), (n, s) in sorted(agg.items()):
        print(f"  {m} {t}: {n}건 {s:,}원")
    print("\n[부호 이상 점검]")
    print("  지출인데 양수:", sum(1 for r in rows if r["타입"] == "지출" and amt(r) > 0))
    print("  수입인데 음수:", sum(1 for r in rows if r["타입"] == "수입" and amt(r) < 0))
    inner = [r for r in rows if r["대분류"] == "내계좌이체"]
    print(f"  내계좌이체: {len(inner)}건, 합계 {sum(amt(r) for r in inner):,}원 (0에 가까울수록 양쪽 기록이 짝이 맞음)")
    print("  타입=이체 건수:", sum(1 for r in rows if r["타입"] == "이체"), " 이 중 대분류=내계좌이체 아닌 것:",
          sum(1 for r in rows if r["타입"] == "이체" and r["대분류"] != "내계좌이체"))
    key = Counter((r["날짜"], r["시간"], r["금액"], r["결제수단"], r["내용"]) for r in rows)
    print("  완전히 같은 행(날짜·시간·금액·결제수단·내용) 중복 건수:", sum(v - 1 for v in key.values() if v > 1))
    print("  자정~새벽 5시 거래:", sum(1 for r in rows if r["시간"] < "05:00"))
    print("  월 마지막 날 22시 이후 거래:", sum(1 for r in rows if r["시간"] >= "22:00" and r["날짜"][8:10] in ("28", "29", "30", "31")))


if __name__ == "__main__":
    main(sys.argv[1])
