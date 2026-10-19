"""후보 모델을 '이 계정에서 실제로 호출되는지' 확인한다.

models 목록에 있어도 호출은 404일 수 있다(2026-10-19 확인). 그래서 목록이 아니라 '실제 호출'로 거른다.
추론형 모델은 짧은 호출(max_tokens=16)에서 내용이 비어 돌아올 수 있어, 실제 실험 질문으로 시험하는 --task 가 있다.

사용
  python experiment/probe_models.py                      # 내장 후보 전체, 짧은 호출
  python experiment/probe_models.py ID1 ID2              # 지정한 모델만
  python experiment/probe_models.py --task ID1 ID2       # 실제 실험 질문(Q01, 조건 B) 1개로 시험: SQL이 나오는지, 실행되는지, 맞는지
  옵션: --max-tokens N (기본: 짧은 호출 16, --task 1024), --timeout 초 (기본 120)
환경 변수 NVIDIA_API_KEY 필요. 출력에는 키와 계정 정보가 나오지 않는다.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from llm_client import LLMClient, LLMError

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent

LARGE = [
    "mistralai/mistral-large", "mistralai/mistral-large-2-instruct", "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1", "nvidia/nemotron-4-340b-instruct", "ai21labs/jamba-1.5-large-instruct",
    "databricks/dbrx-instruct", "01-ai/yi-large", "openai/gpt-oss-20b", "google/gemma-4-31b-it", "z-ai/glm-5.3",
    "deepseek-ai/deepseek-v4.1-flash", "nvidia/nemotron-3-super-120b-a12b",
]
SMALL = [
    "google/gemma-3-12b-it", "google/gemma-3-4b-it", "mistralai/mistral-7b-instruct-v0.3",
    "nv-mistralai/mistral-nemo-12b-instruct", "ibm/granite-3.0-8b-instruct", "microsoft/phi-3.5-moe-instruct",
    "nvidia/nemotron-nano-3-30b-a3b", "z-ai/glm-5.3-flash", "nvidia/mistral-nemo-minitron-8b-8k-instruct",
]


def explain_error(e):
    """오류를 한 줄로 요약한다. 계정·함수 식별자는 지운다."""
    msg = re.sub(r"(account|Function) '[^']*'", r"\1 '***'", str(e))
    m = re.search(r"HTTP (\d+)", msg)
    code = m.group(1) if m else "?"
    hint = {"404": "이 계정에서 호출 불가", "401": "인증 실패", "403": "권한 없음", "429": "한도 초과"}.get(code)
    if hint:
        return f"HTTP {code} ({hint})"
    return f"HTTP {code} 기타: {msg[:140]}"


def quick_probe(model, max_tokens, timeout):
    try:
        c = LLMClient(model, temperature=0.0, max_tokens=max_tokens, rpm=20, timeout=timeout, max_retries=2)
        r = c.chat("숫자만 답하세요.", "1+1은?")
        text = r["text"].strip().replace("\n", " ")[:24]
        extra = f" 종료={r['finish_reason']} 추론글자={r['reasoning_chars']}"
        note = "" if text else "  (내용 비어 있음: 추론형이면 --task 로 확인)"
        return True, f"{r['latency_s']:>5.1f}s 응답={text!r}{extra}{note}"
    except LLMError as e:
        return False, explain_error(e)


def task_probe(model, max_tokens, timeout):
    """실제 실험 프롬프트(Q01, 조건 B)로 한 번 시험한다."""
    import run_experiment as R
    q = json.loads((HERE / "questions_A.json").read_text(encoding="utf-8"))[0]
    kd = json.loads((HERE / "kpi_dictionary_A.json").read_text(encoding="utf-8"))
    user = R.build_user_prompt("B", R.load_schema(), R.render_dictionary(kd), q["question"])
    try:
        c = LLMClient(model, temperature=0.0, max_tokens=max_tokens, rpm=20, timeout=timeout, max_retries=2)
        r = c.chat(R.SYSTEM_PROMPT, user)
    except LLMError as e:
        return False, explain_error(e)
    sql = R.extract_sql(r["text"])
    block = R.safety_check(sql)
    if block:
        status, ok = f"차단:{block}", False
    else:
        try:
            value, st = R.execute_readonly(sql)
            ok = R.is_match(q["metric"], value, q["gold_value"])
            status = f"{st} 정답={'O' if ok else 'X'}"
        except Exception as e:  # noqa: BLE001
            status, ok = f"SQL오류:{str(e)[:50]}", False
    tok = f" 토큰(입/출)={r['prompt_tokens']}/{r['completion_tokens']}"
    note = " 병합전송" if r.get("system_merged") else ""
    return True, (f"{r['latency_s']:>5.1f}s {status} 종료={r['finish_reason']} 추론글자={r['reasoning_chars']}"
                  f" 응답길이={len(r['text'])}{tok}{note}")


def main():
    if not os.environ.get("NVIDIA_API_KEY"):
        print("오류: 환경 변수 NVIDIA_API_KEY 가 설정되지 않았습니다.")
        print('  PowerShell: $env:NVIDIA_API_KEY = Read-Host "NVIDIA API key" -MaskInput')
        sys.exit(1)
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*")
    ap.add_argument("--task", action="store_true")
    ap.add_argument("--max-tokens", type=int)
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()
    mt = a.max_tokens or (1024 if a.task else 16)
    fn = task_probe if a.task else quick_probe
    groups = [("지정", a.models)] if a.models else [("큰 모델 후보", LARGE), ("작은 모델 후보", SMALL)]
    ok_models = []
    print(f"방식: {'실제 질문(Q01, 조건 B)' if a.task else '짧은 호출'}, max_tokens={mt}, timeout={a.timeout}s")
    for name, models in groups:
        print(f"\n[{name}]")
        for m in models:
            good, msg = fn(m, mt, a.timeout)
            print(f"  {'OK  ' if good else 'FAIL'} {m:44s} {msg}")
            if good:
                ok_models.append(m)
    print(f"\n호출 성공 {len(ok_models)}개:")
    for m in ok_models:
        print("  ", m)


if __name__ == "__main__":
    try:
        main()
    except LLMError as e:
        print("오류:", e)
        sys.exit(1)
