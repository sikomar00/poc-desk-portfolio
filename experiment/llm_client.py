"""LLM 호출 래퍼 (표준 라이브러리만 사용). 모델·주소·키를 설정으로 바꿀 수 있다 (결정 D-007).

환경 변수
  NVIDIA_API_KEY   필수(실제 호출 시). 코드·로그·기록 어디에도 남기지 않는다.
  LLM_BASE_URL     기본 https://integrate.api.nvidia.com/v1  (OpenAI 호환 주소면 다른 곳으로 교체 가능)
  LLM_MODEL        모델 ID. 예: meta/llama-3.3-70b-instruct  (--model 로도 지정)

공식 문서로 확인한 것 (docs/06 §10 참조)
  - POST {base}/chat/completions, 인증은 `Authorization: Bearer <키>`
  - 호출 한도는 모델·시간대에 따라 다르며 build.nvidia.com 계정 화면에서 확인(공식 FAQ).
    커뮤니티에서는 약 40 RPM으로 알려져 있으나 공식 수치는 아님 -> 기본 RPM을 보수적으로 둔다.

사용: python experiment/llm_client.py models      # 사용 가능한 모델 목록 조회(키 필요, 미검증 기능)
      python experiment/llm_client.py ping MODEL  # 짧은 호출 한 번으로 연결 확인
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


class LLMError(RuntimeError):
    pass


def _scrub(text, key):
    """오류 메시지에 키가 섞여 나오지 않게 지운다."""
    return text.replace(key, "***") if key else text


class LLMClient:
    def __init__(self, model, temperature=0.7, max_tokens=512, rpm=30, base_url=None,
                 timeout=60, max_retries=5, merge_system=False):
        self.model = model
        self.merge_system = merge_system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.min_interval = 60.0 / rpm  # 호출 사이 최소 간격(초): 한도 초과(429) 예방
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._key = os.environ.get("NVIDIA_API_KEY", "")
        if not self._key:
            raise LLMError("환경 변수 NVIDIA_API_KEY 가 설정되지 않았습니다. (키를 코드나 파일에 적지 마세요)")

    def _wait_turn(self):
        """여러 스레드가 동시에 호출해도 호출 '시작' 간격은 min_interval 이상 벌어지게 한다."""
        with self._lock:
            start = max(time.monotonic(), self._next_slot)
            self._next_slot = start + self.min_interval
        wait = start - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _request(self, path, body=None):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "Accept": "application/json"})
        last = None
        for attempt in range(self.max_retries):
            self._wait_turn()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = _scrub(e.read().decode("utf-8", "replace")[:300], self._key)
                last = f"HTTP {e.code}: {detail}"
                if e.code in (400, 401, 403, 404, 422):  # 재시도해도 소용없는 오류
                    raise LLMError(last) from None
                retry_after = e.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt + 1)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = _scrub(f"{type(e).__name__}: {e}", self._key)
                delay = 2 ** (attempt + 1)
            time.sleep(min(delay, 60))
        raise LLMError(f"재시도 {self.max_retries}회 실패: {last}")

    def _messages(self, system, user):
        if self.merge_system:  # system 역할을 지원하지 않는 모델(예: Gemma 계열 일부 호스팅)용
            return [{"role": "user", "content": f"{system}\n\n{user}"}]
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def chat(self, system, user, meta=None):
        """meta 는 모의(Mock) 클라이언트용이며 실제 호출에는 쓰이지 않는다.

        system 역할 때문에 400/422 가 나면 system 을 user 앞에 합쳐 한 번만 재시도하고,
        그 사실을 반환값(system_merged)에 남긴다. 이후 호출은 같은 형식을 유지한다.
        """
        t0 = time.monotonic()
        body = {"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens}
        try:
            out = self._request("/chat/completions", {**body, "messages": self._messages(system, user)})
        except LLMError as e:
            msg = str(e).lower()
            if self.merge_system or not (("http 400" in msg or "http 422" in msg) and "system" in msg):
                raise
            self.merge_system = True
            out = self._request("/chat/completions", {**body, "messages": self._messages(system, user)})
        try:
            choice = out["choices"][0]
            text = choice["message"]["content"] or ""
            msg = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"예상 밖의 응답 형식: {str(out)[:200]}") from None
        # 추론형 모델은 생각 과정을 별도 필드로 돌려주기도 한다(길이만 기록, 내용은 버림)
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        usage = out.get("usage") or {}
        return {"text": text, "latency_s": round(time.monotonic() - t0, 3), "system_merged": self.merge_system,
                "finish_reason": choice.get("finish_reason"), "reasoning_chars": len(reasoning),
                "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                "model": out.get("model", self.model)}

    def list_models(self):
        out = self._request("/models")
        return sorted(m.get("id", "?") for m in out.get("data", []))


def _main():
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "models":
        ids = LLMClient(model="-").list_models()
        print(f"{len(ids)}개 모델")
        for i in ids:
            print(" ", i)
    elif cmd == "ping" and len(sys.argv) > 2:
        # 연결 확인용이라 오래 매달리지 않는다: 호출당 최대 120초, 재시도 2회
        r = LLMClient(model=sys.argv[2], temperature=0.0, max_tokens=16, timeout=120,
                      max_retries=2).chat("숫자만 답하세요.", "1+1은?")
        print("응답:", r["text"].strip(), f"({r['latency_s']}s)")
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        _main()
    except LLMError as e:
        print("오류:", e)
        sys.exit(1)
