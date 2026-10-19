"""PoC Desk 엔진과 서버 테스트.  실행: python app/test_app.py   (추가 설치 없음, 데이터는 임시 폴더에 쓴다)"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="pocdesk-test-")
os.environ["POC_DESK_DATA_DIR"] = str(Path(TMP) / "data")
os.environ["POC_DESK_REPORT_DIR"] = str(Path(TMP) / "reports")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
import server  # noqa: E402

QUESTIONS = json.loads((engine.ROOT / "experiment" / "questions_A.json").read_text(encoding="utf-8"))


class FakeClient:
    """LLM 대신 정해진 답을 돌려주는 가짜 클라이언트 (네트워크 호출 없음)."""
    def __init__(self, text):
        self.text = text

    def chat(self, system, user, meta=None):
        FakeClient.last_user = user
        return {"text": self.text, "latency_s": 0.0, "prompt_tokens": None, "completion_tokens": None}


class Interpretation(unittest.TestCase):
    def test_months(self):
        cases = {"1월과 2월 매출을 합치면 얼마야?": ["2026-01", "2026-02"], "1~2월 환불률은?": ["2026-01", "2026-02"],
                 "2~3월에 주문 하나당 평균 얼마?": ["2026-02", "2026-03"], "1분기 매출 합계는?": engine.VALID_MONTHS,
                 "2026년 3월 취소율": ["2026-03"], "매출이 얼마야?": []}
        for text, want in cases.items():
            self.assertEqual(engine.parse_months(text)["months"], want, text)
        self.assertEqual(engine.parse_months("4월 매출")["invalid"], ["2026-04"])

    def test_metrics(self):
        cases = {"2월에 얼마 벌었어?": ["net_revenue"], "1월 환불 반영한 매출은?": ["net_revenue"],
                 "2월에 판매한 것 중 환불로 나간 비중은?": ["refund_rate"], "3월에 주문 몇 건 들어왔어?": ["paid_orders"],
                 "2월 주문 중 취소된 비율은?": ["cancel_rate"], "2월 객단가는?": ["aov"], "재구매율은 얼마야?": []}
        for text, want in cases.items():
            self.assertEqual([c["key"] for c in engine.detect_metrics(text)], want, text)
        self.assertEqual(len(engine.detect_metrics("취소된 주문 몇 건이야?")), 2)  # 모호하면 사용자가 고르게 한다

    def test_intake_validation(self):
        with self.assertRaises(ValueError):
            engine.intake("   ")
        self.assertEqual(engine.intake("재구매율은?")["status"], "unknown")
        self.assertEqual(engine.intake("취소된 주문 몇 건이야?")["status"], "ambiguous")


class ReferenceMode(unittest.TestCase):
    def test_all_twenty_questions_match_gold(self):
        for q in QUESTIONS:
            r = engine.run_query(q["question"], q["metric"], q["months"], "reference")
            self.assertAlmostEqual(r["value"], float(q["gold_value"]), places=2, msg=q["id"])
            self.assertNotEqual(r["overall"], "ERROR", q["id"])

    def test_revenue_is_reconciled_with_ledger_exactly(self):
        r = engine.run_query("2월 매출", "net_revenue", ["2026-02"], "reference")
        self.assertEqual(r["value"], 18287000)
        self.assertEqual(r["overall"], "PASS")
        self.assertEqual(r["checks"][0]["name"], "원장 대사")

    def test_other_metrics_have_no_ledger_so_not_marked_verified(self):
        r = engine.run_query("1분기 환불률", "refund_rate", engine.VALID_MONTHS, "reference")
        self.assertEqual(r["overall"], "INFO")  # '통과'가 아니다
        self.assertEqual(r["unit"], "%")

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            engine.run_query("x", "net_revenue", [], "reference")
        with self.assertRaises(ValueError):
            engine.run_query("x", "net_revenue", ["2026-04"], "reference")
        with self.assertRaises(ValueError):
            engine.run_query("x", "no_such_metric", ["2026-01"], "reference")  # 사전에 없는 지표는 거부
        with self.assertRaises(ValueError):
            engine.run_query("x", "net_revenue", ["2026-01"], "magic")


class Dictionary(unittest.TestCase):
    def test_four_fields_required_and_versioning(self):
        with self.assertRaises(ValueError) as ctx:
            engine.save_definition("재구매율", [], "a", "", "c", "")
        self.assertIn("포함·제외", str(ctx.exception))
        with self.assertRaises(ValueError):
            engine.save_definition("매출", [], "a", "b", "c", "d")  # 내장 지표 이름
        m1 = engine.save_definition("재구매율", ["리피트율"], "재구매 고객 ÷ 전체 고객", "취소 제외", "결제일", "%")
        m2 = engine.save_definition("재구매율", [], "재구매 고객 ÷ 결제 고객", "취소 제외", "결제일", "%")
        self.assertEqual((m1["version"], m2["version"]), ("v1", "v2"))
        stored = [m for m in engine.dictionary() if m["key"] == m1["key"]][0]
        self.assertEqual(len(stored["history"]), 1)
        self.assertFalse(stored["has_reference"])
        with self.assertRaises(ValueError):  # 기준 계산식이 없는 지표는 계산식 모드 불가
            engine.run_query("재구매율", m1["key"], ["2026-01"], "reference")


class AiMode(unittest.TestCase):
    def setUp(self):
        self._orig = (engine._make_client, engine.llm_key_status)
        engine.llm_key_status = lambda: True

    def tearDown(self):
        engine._make_client, engine.llm_key_status = self._orig

    def _ai(self, sql_text, metric="net_revenue", months=("2026-02",), q="2월 매출"):
        engine._make_client = lambda model: FakeClient(sql_text)
        return engine.run_query(q, metric, list(months), "ai", "stub-model")

    def test_correct_sql_passes_both_checks(self):
        gold = engine.gold_sql("net_revenue", ["2026-02"])
        r = self._ai(f"```sql\n{gold}\n```")
        self.assertEqual(r["overall"], "PASS")
        self.assertEqual(sorted(c["name"] for c in r["checks"]), ["기준 계산식 대조", "원장 대사"])
        self.assertIn("확정한 기간: 2026-02", FakeClient.last_user)  # 확정한 기간이 프롬프트에 전달됨

    def test_prompt_never_contains_ledger_or_rows(self):
        self._ai("```sql\nSELECT 1\n```")
        self.assertNotIn("ledger", FakeClient.last_user)
        self.assertNotIn("CREATE TABLE ledger", FakeClient.last_user)

    def test_wrong_sql_is_flagged(self):
        r = self._ai("```sql\nSELECT SUM(total_amount) FROM orders\n```")
        self.assertEqual(r["overall"], "FLAG")
        self.assertTrue(any(c["status"] == "FLAG" for c in r["checks"]))

    def test_unsafe_sql_is_blocked_and_logged(self):
        for text, why in (("```sql\nDELETE FROM orders\n```", "SELECT로 시작하지 않음"),
                          ("```sql\nSELECT net_revenue_ex_vat FROM ledger\n```", "정답표")):
            r = self._ai(text)
            self.assertEqual((r["status"], r["overall"]), ("BLOCKED", "ERROR"))
            self.assertIn(why, r["error"])
        self.assertTrue(any(h["status"] == "BLOCKED" for h in engine.history()))

    def test_custom_metric_is_never_marked_verified(self):
        m = engine.save_definition("신규 지표 X", [], "무언가", "없음", "결제일", "건")
        r = self._ai("```sql\nSELECT 42\n```", metric=m["key"], months=("2026-01",), q="X는?")
        self.assertEqual(r["overall"], "UNVERIFIED")
        self.assertNotIn("PASS", [c["status"] for c in r["checks"]])
        self.assertIn("신규 지표 X", FakeClient.last_user)  # 사용자 정의가 사전 텍스트로 전달됨

    def test_missing_key_gives_clear_error(self):
        engine.llm_key_status = lambda: False
        r = engine.run_query("2월 매출", "net_revenue", ["2026-02"], "ai", "m")
        self.assertEqual(r["overall"], "ERROR")
        self.assertIn("NVIDIA_API_KEY", r["error"])
        self.assertNotIn("nvapi", r["error"])


class Report(unittest.TestCase):
    def test_report_contains_sections(self):
        r = engine.run_query("1분기 환불률은?", "refund_rate", engine.VALID_MONTHS, "reference")
        rep = engine.make_report(r["id"])
        for needle in ("# PoC 리포트", "## 결과", "## 대사(검증) 결과", "## 사용한 정의", "```sql", "합성(가짜)"):
            self.assertIn(needle, rep["markdown"])
        self.assertTrue((Path(TMP) / "reports" / rep["filename"]).exists())
        with self.assertRaises(ValueError):
            engine.make_report("없는-id")


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.make_server(0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        return r.status, dict(r.getheaders()), data

    def post(self, path, obj, **kw):
        h = {"Content-Type": "application/json", "X-PocDesk": "1"}
        h.update(kw.pop("headers", {}))
        return self.req("POST", path, json.dumps(obj).encode(), h, **kw)

    def test_pages_and_security_headers(self):
        s, h, d = self.req("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn("PoC Desk", d.decode())
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(self.req("GET", "/app.js")[0], 200)
        self.assertEqual(self.req("GET", "/style.css")[0], 200)
        self.assertEqual(self.req("GET", "/../engine.py")[0], 404)
        self.assertEqual(self.req("GET", "/%2e%2e/engine.py")[0], 404)

    def test_rejects_foreign_host_and_origin(self):
        self.assertEqual(self.req("GET", "/api/status", host="evil.example.com")[0], 403)  # DNS 리바인딩 방어
        self.assertEqual(self.post("/api/intake", {"question": "2월 매출"}, headers={"Origin": "http://evil.example.com"})[0], 403)

    def test_post_requires_json_and_custom_header(self):
        self.assertEqual(self.req("POST", "/api/intake", b'{"question":"x"}', {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.req("POST", "/api/intake", b"question=x", {"X-PocDesk": "1", "Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.req("POST", "/api/intake", b"{bad", {"X-PocDesk": "1", "Content-Type": "application/json"})[0], 400)
        big = b'{"question":"' + b"x" * 200_000 + b'"}'
        self.assertEqual(self.req("POST", "/api/intake", big, {"X-PocDesk": "1", "Content-Type": "application/json"})[0], 413)

    def test_end_to_end_flow(self):
        s, _, d = self.post("/api/intake", {"question": "2월에 얼마 벌었어?"})
        intake = json.loads(d)
        self.assertEqual((s, intake["candidates"][0]["key"], intake["months"]), (200, "net_revenue", ["2026-02"]))
        s, _, d = self.post("/api/run", {"question": "2월에 얼마 벌었어?", "metric_key": "net_revenue",
                                          "months": ["2026-02"], "mode": "reference"})
        run = json.loads(d)
        self.assertEqual((s, run["value"], run["overall"]), (200, 18287000, "PASS"))
        s, _, d = self.post("/api/report", {"run_id": run["id"]})
        self.assertEqual(s, 200)
        self.assertIn("18,287,000원", json.loads(d)["markdown"])
        s, _, d = self.req("GET", "/api/history")
        self.assertTrue(json.loads(d)["runs"])
        s, _, d = self.post("/api/run", {"question": "x", "metric_key": "nope", "months": ["2026-01"]})
        self.assertEqual(s, 400)
        self.assertIn("사전에 없는", json.loads(d)["error"])
        s, _, d = self.post("/api/definition", {"name": "Z", "formula": "a"})
        self.assertEqual(s, 400)

    def test_status_does_not_leak_key(self):
        os.environ["NVIDIA_API_KEY"] = "dummy-secret-value-for-test"
        try:
            s, _, d = self.req("GET", "/api/status")
            self.assertNotIn("secret", d.decode())
            self.assertTrue(json.loads(d)["llm_key"])
        finally:
            del os.environ["NVIDIA_API_KEY"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
