"""앱 화면의 월별 지출 합계와, 정의별로 계산한 월 지출을 대조한다 (로컬 전용).

준비: data/private/app_monthly_totals.csv 를 채운다 (양식은 같은 파일 참고).
  month,app_spending
  2025-11,1234567        ← 앱 화면의 그 달 '지출' 합계(원, 양수). 모르는 달은 비워 둠
실행: python data/compare_with_app.py [csv 경로]

출력: 콘솔에는 '앱 합계 대비 차이 비율'만, 금액이 있는 표는 data/private/app_compare_amounts.txt 에 저장.
"""
import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
PRIV = HERE / "private"

spec = importlib.util.spec_from_file_location("cv", HERE / "compute_spending_variants.py")
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)


def load_app(path):
    app = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        head = f.readline()
        f.seek(0)
        delim = max(["	", ",", ";"], key=head.count)  # 엑셀에서 붙여 넣으면 탭 구분이 된다
        for r in csv.DictReader(f, delimiter=delim):
            v = (r.get("app_spending") or "").replace(",", "").strip()
            if v:
                app[r["month"].strip()] = abs(int(float(v)))
    return app


def main(path):
    app = load_app(path)
    if not app:
        print("app_spending 이 모두 비어 있음. 값을 채운 뒤 다시 실행하세요.")
        raise SystemExit(1)
    con = sqlite3.connect(PRIV / "bank.db")
    res = {n: dict(con.execute(sql).fetchall()) for n, sql in cv.VARIANTS.items()}
    months = sorted(app)
    print(f"대조 월 수: {len(months)} ({months[0]} ~ {months[-1]})")
    print("\n[앱 합계 대비 차이 비율] 평균절대오차(MAPE) 오름차순 — 앱이 쓰는 정의에 가까운 순서")
    print("| 정의 | MAPE | 월별 최소 | 월별 최대 | 정확히 일치한 달 |")
    print("|---|---:|---:|---:|---:|")
    rows = []
    for n, r in res.items():
        diffs = [(r.get(m, 0) / app[m] - 1) * 100 for m in months if app[m]]
        exact = sum(1 for m in months if int(r.get(m, 0)) == app[m])
        rows.append((sum(abs(d) for d in diffs) / len(diffs), n, min(diffs), max(diffs), exact))
    for mape, n, lo, hi, exact in sorted(rows):
        print(f"| {n} | {mape:.1f}% | {lo:+.1f}% | {hi:+.1f}% | {exact}/{len(months)} |")
    with open(PRIV / "app_compare_amounts.txt", "w", encoding="utf-8") as f:  # 금액 포함: 비공개
        f.write(f"{'정의':60s}" + "".join(f"{m:>12s}" for m in months) + "\n")
        f.write(f"{'앱 화면 합계':60s}" + "".join(f"{app[m]:>12,d}" for m in months) + "\n")
        for n, r in res.items():
            f.write(f"{n:60s}" + "".join(f"{int(r.get(m, 0)):>12,d}" for m in months) + "\n")
    print("\n금액 표 저장: data/private/app_compare_amounts.txt (Git 제외)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else PRIV / "app_monthly_totals.csv")
