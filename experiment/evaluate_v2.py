"""사전 v2 실험의 '사전 지정 평가'. 실험을 돌리기 전에 판정 규칙을 이 파일에 고정해 둔다.

질문 1  사전 v2가 사전 v1보다 보류 질문에서 정확한가?  (표적 5문항 / 대조 5문항 / 부작용 확인)
질문 2  소급 시험에서 고른 검증 방법 1(교차 모델 불일치)과 3'(반복 4/5 미만 일치)가 새 질문에서도 쓸 만한가?

규칙은 아래 상수와 함수로 고정한다. 결과를 본 뒤 바꾸려면 먼저 docs/02-thinking-log.md 에 사유를 적는다.
사용: python experiment/evaluate_v2.py [RUN1.jsonl RUN2.jsonl]   # 파일을 안 주면 runs/ 의 최신 holdout 기록 모델별 1개
      python experiment/evaluate_v2.py --mock                    # 모의 기록(runs_mock/)으로 코드 동작만 확인
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_experiment as R  # noqa: E402  (채점 허용 오차, 해시, 사전 렌더링을 실험과 똑같이 쓴다)

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent

# ---- 사전 고정 규칙 (실행 전) ----
GAIN_TARGETED_PP = 20.0    # 표적 질문 정확도가 B1보다 B2에서 이만큼(%p) 이상 오르고
REGRESSION_CONTROL_PP = -5.0  # 대조 질문 정확도가 B1 대비 이 값(%p)보다 더 떨어지지 않으면 '개선(회귀 없음)'
NO_GAIN_PP = 5.0           # 표적 질문 개선이 이 값 미만이면 '무개선'
MIN_WRONG_FOR_VERDICT = 10  # 분석 대상 오답이 이보다 적으면 방법 평가는 '표본 부족'
LIFT_OK, LIFT_FAIL = 1.5, 1.2  # 경보 정밀도가 기준선의 몇 배 이상이면 재현, 미만이면 실패
DETECT_OK = 0.60           # 오답 검출률이 이 이상이어야 재현
REPEAT_NEED = 4            # 방법 3': 5회 중 이 횟수 이상 일치해야 통과


def load_runs(paths):
    data = {}
    for p in paths:
        rows = [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
        meta = next(r for r in rows if r.get("type") == "meta")
        if meta.get("suite") != "holdout":
            sys.exit(f"holdout 기록이 아닙니다: {p}")
        # 같은 호출을 다시 시도한 기록이 있으면 마지막 것만 쓴다
        last = {}
        for r in rows:
            if r.get("type") == "run":
                last[r["run_key"]] = r
        # 서비스 장애(llm_error)는 모델의 오답이 아니므로 분석에서 제외하고 개수를 보고한다 (사전 고정 규칙, D-027)
        infra = [r for r in last.values() if r["status"] == "llm_error"]
        if infra:
            print(f"알림: {meta['model']} 의 서비스 장애 호출 {len(infra)}회는 분석에서 제외합니다 "
                  f"(같은 --run-id 로 다시 실행하면 재시도됩니다)")
        data[meta["model"]] = (meta, [r for r in last.values() if r["status"] != "llm_error"])
    return data


def find_latest(mock):
    d = HERE / ("runs_mock" if mock else "runs")
    best = {}
    for f in sorted(d.glob("*.jsonl")):
        first = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
        if first.get("suite") == "holdout":
            best[first["model"]] = f
    return list(best.values())


def check_frozen(metas):
    """질문과 사전이 실험 전에 동결한 내용과 같은 것으로 실행됐는지 확인한다."""
    q = json.loads((HERE / "questions_A_holdout.json").read_text(encoding="utf-8"))
    qh = R.sha(json.dumps(q, ensure_ascii=False, sort_keys=True))
    hashes = {c: R.sha(R.render_dictionary(json.loads((HERE / f).read_text(encoding="utf-8"))))
              for c, f in R.SUITES["holdout"]["conds"].items()}
    for meta in metas:
        if meta["questions_hash"] != qh or meta["dictionary_hashes"] != hashes:
            sys.exit("동결 확인 실패: 실행 당시의 질문·사전이 지금 파일과 다릅니다. 평가를 중단합니다.")
    print("동결 확인: 모든 실행이 현재의 보류 질문·사전 v1·v2와 같은 내용으로 수행됨\n")


def pct(k, n):
    return f"{k}/{n} = {k / n * 100:.0f}%" if n else "-"


def acc(runs, cond, group, qgroup):
    rs = [r for r in runs if r["cond"] == cond and qgroup[r["qid"]] == group] if group else [r for r in runs if r["cond"] == cond]
    return sum(r["correct"] for r in rs), len(rs)


def verdict_primary(gain_t, delta_c):
    if gain_t >= GAIN_TARGETED_PP and delta_c >= REGRESSION_CONTROL_PP:
        return "개선(회귀 없음)"
    if gain_t >= GAIN_TARGETED_PP:
        return "개선되었으나 대조 질문에서 회귀"
    if gain_t < NO_GAIN_PP:
        return "무개선"
    return "불명"


# ---- 방법 1, 3' (verify_without_gold.py 와 같은 규칙) ----
def agree(metric, a, b):
    return a is not None and b is not None and abs(a - b) <= R.TOLERANCE[metric]


def flags_method1(by_model, cond, metric_of):
    flagged = set()
    models = list(by_model)
    for m in models:
        other = [x for x in models if x != m][0]
        idx = {(r["qid"], r["rep"]): r for r in by_model[other] if r["cond"] == cond}
        for r in by_model[m]:
            if r["cond"] != cond:
                continue
            o = idx.get((r["qid"], r["rep"]))
            if o is None or not agree(metric_of[r["qid"]], r["value"], o["value"]):
                flagged.add((m, r["qid"], r["rep"]))
    return flagged


def flags_method3(by_model, cond, metric_of):
    flagged = set()
    for m, runs in by_model.items():
        by_q = defaultdict(list)
        for r in runs:
            if r["cond"] == cond:
                by_q[r["qid"]].append(r)
        for q, rs in by_q.items():
            vals = [r["value"] for r in rs]
            best = max((sum(agree(metric_of[q], a, b) for b in vals) for a in vals if a is not None), default=0)
            if best < REPEAT_NEED:
                for r in rs:
                    flagged.add((m, q, r["rep"]))
    return flagged


def eval_method(name, flagged, by_model, cond):
    answers = [(m, r) for m, runs in by_model.items() for r in runs if r["cond"] == cond and r["value"] is not None]
    wrong = [(m, r) for m, r in answers if not r["correct"]]
    right = [(m, r) for m, r in answers if r["correct"]]
    key = lambda m, r: (m, r["qid"], r["rep"])
    det = sum(key(m, r) in flagged for m, r in wrong)
    fa = sum(key(m, r) in flagged for m, r in right)
    n_flag = det + fa
    base = len(wrong) / len(answers) if answers else 0
    prec = det / n_flag if n_flag else 0
    lift = prec / base if base else 0
    detect = det / len(wrong) if wrong else 0
    if len(wrong) < MIN_WRONG_FOR_VERDICT:
        v = f"표본 부족(오답 {len(wrong)} < {MIN_WRONG_FOR_VERDICT}), 판정 없음"
    elif lift >= LIFT_OK and detect >= DETECT_OK:
        v = "재현됨"
    elif lift < LIFT_FAIL:
        v = "재현 실패"
    else:
        v = "불명"
    print(f"  [{cond}] {name}: 검출 {pct(det, len(wrong))}, 거짓경보 {pct(fa, len(right))}, 경보 정밀도 {prec * 100:.0f}% "
          f"(기준선 {base * 100:.0f}%, {lift:.1f}배) -> {v}")
    return v


def main():
    mock = "--mock" in sys.argv
    paths = [a for a in sys.argv[1:] if not a.startswith("--")] or find_latest(mock)
    if len(paths) != 2:
        sys.exit(f"holdout 기록이 모델당 1개씩 총 2개 필요합니다(찾은 파일 {len(paths)}개)")
    data = load_runs(paths)
    metas = [m for m, _ in data.values()]
    if any(m["model"].startswith("MOCK") for m in metas):
        print("*** MOCK 기록: 실험 결과가 아니라 코드 동작 확인용입니다 ***\n")
    check_frozen(metas)

    questions = json.loads((HERE / "questions_A_holdout.json").read_text(encoding="utf-8"))
    qgroup = {q["id"]: q["group"] for q in questions}
    metric_of = {q["id"]: q["metric"] for q in questions}
    by_model = {m: runs for m, (_, runs) in data.items()}
    all_runs = [r for runs in by_model.values() for r in runs]
    expect = len(questions) * R.REPS * 2
    for m, runs in by_model.items():
        if len(runs) != expect:
            print(f"경고: {m} 의 호출 수가 {len(runs)}회(기대 {expect}회). 미완료 실행이면 판정이 왜곡됩니다.")

    print("== 질문 1: 사전 v2가 v1보다 나은가 (사전 지정 규칙) ==")
    print(f"  규칙: 표적 정확도 +{GAIN_TARGETED_PP:.0f}%p 이상이고 대조 정확도 변화 {REGRESSION_CONTROL_PP:.0f}%p 이상이면 '개선(회귀 없음)', "
          f"표적 개선 {NO_GAIN_PP:.0f}%p 미만이면 '무개선'")
    scopes = [("전체(모델 합산)", all_runs)] + [(m.split("/")[-1], runs) for m, runs in by_model.items()]
    for label, runs in scopes:
        print(f"\n  <{label}>")
        out = {}
        for grp in ("표적", "대조", None):
            name = grp or "전체"
            b1, b2 = acc(runs, "B1", grp, qgroup), acc(runs, "B2", grp, qgroup)
            d = (b2[0] / b2[1] - b1[0] / b1[1]) * 100
            out[name] = d
            lo1, hi1 = R.wilson(*b1)
            lo2, hi2 = R.wilson(*b2)
            print(f"    {name:3s} 질문  B1(v1) {pct(*b1):>14s} [{lo1 * 100:.0f}~{hi1 * 100:.0f}%]   "
                  f"B2(v2) {pct(*b2):>14s} [{lo2 * 100:.0f}~{hi2 * 100:.0f}%]   차이 {d:+.0f}%p")
        print(f"    판정: {verdict_primary(out['표적'], out['대조'])}")
    print("\n  지표별 (모델 합산, 5회×2모델=10회 중 정답):")
    for metric in R.TOLERANCE:
        qs = [q["id"] for q in questions if q["metric"] == metric]
        if not qs:
            continue
        cells = []
        for cond in ("B1", "B2"):
            rs = [r for r in all_runs if r["cond"] == cond and r["qid"] in qs]
            cells.append(f"{sum(r['correct'] for r in rs)}/{len(rs)}")
        print(f"    {metric:12s} 질문 {len(qs)}개  B1 {cells[0]:>6s} -> B2 {cells[1]:>6s}")
    print("\n  일치율(참고, 판정 규칙 아님): " + ", ".join(
        f"{m.split('/')[-1]} {c} "
        f"{sum(R.consistency([r['value'] for r in runs if r['cond'] == c and r['qid'] == q['id']], R.TOLERANCE[q['metric']]) for q in questions) / len(questions) * 100:.0f}%"
        for m, runs in by_model.items() for c in ("B1", "B2")))

    print(f"\n== 질문 2: 검증 방법 1·3'의 재현 (사전 지정 규칙) ==")
    print(f"  규칙: 경보 정밀도 ≥ 기준선의 {LIFT_OK}배 이고 오답 검출 ≥ {DETECT_OK * 100:.0f}% 면 '재현됨', "
          f"정밀도 < {LIFT_FAIL}배면 '재현 실패', 분석 대상 오답 < {MIN_WRONG_FOR_VERDICT}개면 '표본 부족'")
    print("  주 평가 조건은 B1(v1 사전: 소급 시험 때와 오답 비율이 비슷한 조건). B2는 참고(오답이 적어 '표본 부족'일 수 있음)")
    for cond in ("B1", "B2"):
        eval_method("방법 1 교차 모델 불일치", flags_method1(by_model, cond, metric_of), by_model, cond)
        eval_method("방법 3' 반복 4/5 미만 일치", flags_method3(by_model, cond, metric_of), by_model, cond)


if __name__ == "__main__":
    main()
