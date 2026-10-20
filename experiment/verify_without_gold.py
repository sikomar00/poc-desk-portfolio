"""정답 출처가 없는 지표(환불률 등)의 오류를 걸러낼 수 있는가? 세 방법을 기존 실험 기록으로 소급 시험한다 (LLM 호출 없음).

방법 1  이중 계산(교차 모델 일치): 같은 질문을 다른 모델도 풀게 하고 값이 다르면 경보
방법 2  지표 간 관계 검사: 한 모델의 같은 회차 답들끼리 지켜야 할 관계(합계, 객단가 = 매출 / 건수, 범위 등)가 깨지면 경보
방법 3  반복 실행 불일치 경보: 같은 질문 5회 결과가 서로 다르면 경보

중요: 세 방법 모두 '골드(정답)'를 쓰지 않는다. 골드는 오직 방법을 채점하는 데만 쓴다.
대상: experiment/runs/ 의 실제 실행 기록 중 조건 B(KPI 사전 제공) 호출. 값이 없는 호출(SQL 오류 등)은 이미 실패가 드러나므로 제외한다.
한계: 방법을 결과를 본 뒤에 고안했고 같은 데이터로 시험하므로 '탐색적 소급 시험'이다.

실행: python experiment/verify_without_gold.py
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # 출력이 파일·테스트용 버퍼로 바뀐 상태에서 불러와도 죽지 않게
    sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verification as V  # noqa: E402  (방법 1·3 규칙은 사전 지정 평가기와 같은 코드를 쓴다)
from verification import agree  # noqa: E402

METRIC_OF = {q["id"]: q["metric"] for q in json.loads((HERE / "questions_A.json").read_text(encoding="utf-8"))}


def load(cond="B"):
    """본 실험(main) 기록만 읽는다. 같은 모델 이름의 보류 질문(holdout)·스모크 기록이 섞여 들어와
    본 실험 기록을 덮어쓰면 안 된다. 모델마다 호출이 가장 많은 기록 하나를 쓴다."""
    best = {}
    for f in sorted((HERE / "runs").glob("*.jsonl")):
        rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        meta = next(r for r in rows if r.get("type") == "meta")
        if meta.get("suite", "main") != "main":
            continue
        runs = {(r["qid"], r["rep"]): r for r in rows if r.get("type") == "run" and r["cond"] == cond}
        if meta["model"] not in best or len(runs) >= len(best[meta["model"]]):
            best[meta["model"]] = runs
    return best


# ---- 방법 1: 교차 모델 일치 ----
def method1(data):
    flagged = set()
    models = list(data)
    for m in models:
        other = [x for x in models if x != m][0]
        for key, r in data[m].items():
            o = data[other].get(key)
            if o is None or V.cross_model_flag(METRIC_OF[key[0]], r["value"], o["value"]):
                flagged.add((m, key))
    return flagged


# ---- 방법 2: 지표 간 관계 검사 ----
def method2(data):
    flagged, violations, instances = set(), Counter(), []
    for m, runs in data.items():
        for rep in range(1, 6):
            v = {q: runs[(q, rep)]["value"] for q in METRIC_OF if (q, rep) in runs}

            def need(*qs):
                return all(v.get(q) is not None for q in qs)

            def flag(rule, *qs):
                violations[rule] += 1
                instances.append((rule, [(m, (q, rep)) for q in qs]))
                for q in qs:
                    flagged.add((m, (q, rep)))

            # 범위: 비율은 0~100, 건수는 0 이상의 정수, 금액은 0 이상, 객단가는 양수
            for q, val in v.items():
                if val is None:
                    continue
                met = METRIC_OF[q]
                bad = ((met in ("refund_rate", "cancel_rate") and not 0 <= val <= 100)
                       or (met == "paid_orders" and (val < 0 or abs(val - round(val)) > 1e-9))
                       or (met == "net_revenue" and val < 0) or (met == "aov" and val <= 0))
                if bad:
                    flag("범위", q)
            # 합계: 1분기 매출 = 1월 + 2월 + 3월 매출
            if need("Q04", "Q01", "Q02", "Q03") and abs(v["Q04"] - (v["Q01"] + v["Q02"] + v["Q03"])) > 1:
                flag("1분기 매출 = 월별 합", "Q04", "Q01", "Q02", "Q03")
            # 객단가 = 매출 / 건수 (월별)
            for aov, rev, cnt in (("Q14", "Q01", "Q18"), ("Q15", "Q02", "Q19"), ("Q16", "Q03", "Q20")):
                if need(aov, rev, cnt) and (v[cnt] == 0 or abs(v[aov] - v[rev] / v[cnt]) > 1.01):
                    flag("객단가 = 매출 / 건수", aov, rev, cnt)
            # 1분기 객단가 = 1분기 매출 합 / 1분기 건수 합
            if need("Q17", "Q01", "Q02", "Q03", "Q18", "Q19", "Q20"):
                orders = v["Q18"] + v["Q19"] + v["Q20"]
                if orders == 0 or abs(v["Q17"] - (v["Q01"] + v["Q02"] + v["Q03"]) / orders) > 1.01:
                    flag("1분기 객단가 = 합 / 합", "Q17", "Q01", "Q02", "Q03", "Q18", "Q19", "Q20")
            # 1분기 비율은 월별 비율의 최솟값과 최댓값 사이
            for q1, months in (("Q09", ("Q06", "Q07", "Q08")), ("Q13", ("Q10", "Q11", "Q12"))):
                if need(q1, *months):
                    lo, hi = min(v[x] for x in months), max(v[x] for x in months)
                    if not lo - 0.01 <= v[q1] <= hi + 0.01:
                        flag("1분기 비율은 월별 범위 안", q1, *months)
    return flagged, violations, instances


# ---- 방법 3: 반복 실행 불일치 ----
def method3(data, need_agree):
    flagged = set()
    for m, runs in data.items():
        for q, met in METRIC_OF.items():
            vals = [runs[(q, rep)]["value"] for rep in range(1, 6) if (q, rep) in runs]
            if V.repeat_flag(met, vals, need_agree):
                for rep in range(1, 6):
                    flagged.add((m, (q, rep)))
    return flagged


def score(name, flagged, answers, rows):
    wrong = [a for a in answers if not rows[a]["correct"]]
    right = [a for a in answers if rows[a]["correct"]]
    det = sum(a in flagged for a in wrong)
    fa = sum(a in flagged for a in right)
    n_flag = det + fa
    prec = det / n_flag if n_flag else 0.0
    base = len(wrong) / len(answers) if answers else 0.0

    def ratio(n, d):  # 오답이나 정답이 0건인 기록(아주 좋거나 아주 나쁜 경우)에서도 0으로 나누지 않는다
        return f"{n / d * 100:3.0f}%" if d else " n/a"
    lift = f"{prec / base:.1f}배" if base else "n/a"
    print(f"  {name:34s} 검출 {det:>3d}/{len(wrong):<3d}({ratio(det, len(wrong))}) "
          f"거짓경보 {fa:>3d}/{len(right):<3d}({ratio(fa, len(right))}) "
          f"경보 정밀도 {prec * 100:3.0f}% (아무 답이나 찍으면 {base * 100:.0f}%, {lift})")
    return det, fa


def report(cond, detail):
    data = load(cond)
    rows = {(m, k): r for m, runs in data.items() for k, r in runs.items()}
    answers = [a for a, r in rows.items() if r["value"] is not None]
    wrong = [a for a in answers if not rows[a]["correct"]]
    print()
    print(f"{'=' * 8} 조건 {cond} {'(KPI 사전 제공: 실제 운영 조건)' if cond == 'B' else '(사전 없음: 오답이 일관되게 반복되는 조건)'} {'=' * 8}")
    print(f"모델 {[m.split('/')[-1] for m in data]}, 호출 {len(rows)}회, 값 없는 호출 {len(rows) - len(answers)}회 제외 -> 분석 {len(answers)}회 (오답 {len(wrong)}, 정답 {len(answers) - len(wrong)})")
    f1 = method1(data)
    f2, viol, inst = method2(data)
    f3 = method3(data, 5)
    f3b = method3(data, 4)
    anyf = f1 | f2 | f3
    print("[정답을 쓰지 않는 방법의 성능]")
    score("1 교차 모델 불일치", f1, answers, rows)
    score("2 지표 간 관계 위반", f2, answers, rows)
    score("3 반복 5회 불일치(5/5 일치만 통과)", f3, answers, rows)
    score("3' 반복 5회 중 4/5 미만 일치", f3b, answers, rows)
    score("세 방법 중 하나라도 (1+2+3)", anyf, answers, rows)
    if not detail:
        esc = [a for a in wrong if a not in anyf]
        print(f"  세 방법(1+2+3) 모두 못 잡은 오답: {len(esc)}건")
        return
    print()
    print("  방법 2에서 위반된 관계(회차 수):", dict(viol))
    hit = sum(any(a in rows and not rows[a]["correct"] for a in inv) for _, inv in inst)
    print(f"  위반 {len(inst)}건 중 관련 답 중 하나라도 실제 오답인 경우: {hit}건 ({hit / len(inst) * 100:.0f}%)  <- 규칙 단위 정밀도")
    print()
    print("[지표별 오답 검출 (1+2+3 합산) / 각 방법]")
    print(f"  {'지표':12s} {'오답':>4s} {'합산':>5s} {'방법1':>5s} {'방법2':>5s} {'방법3':>5s}  못 잡은 오답")
    for met in ("net_revenue", "paid_orders", "aov", "refund_rate", "cancel_rate"):
        w = [a for a in wrong if METRIC_OF[a[1][0]] == met]
        c = lambda f: sum(a in f for a in w)
        print(f"  {met:12s} {len(w):>4d} {c(anyf):>5d} {c(f1):>5d} {c(f2):>5d} {c(f3):>5d}  {len(w) - c(anyf)}")
    print()
    print(f"  방법 1이 못 잡은 오답 {sum(1 for a in wrong if a not in f1)}건은 두 모델이 같은 틀린 값을 낸 경우(상관된 오류)")
    print()
    print("[참고] 질문마다 5회 중 최빈 값을 답으로 고르면(다수결) 정답인 질문 수")
    for m, runs in data.items():
        ok = 0
        for q, met in METRIC_OF.items():
            reps = [r for (qq, rep), r in sorted(runs.items()) if qq == q and r["value"] is not None]
            if not reps:
                continue
            best = max(reps, key=lambda r: sum(agree(met, r["value"], x["value"]) for x in reps))
            ok += best["correct"]
        print(f"  {m}: {ok}/20 질문")


def main():
    report("B", True)
    report("A", False)


if __name__ == "__main__":
    main()
