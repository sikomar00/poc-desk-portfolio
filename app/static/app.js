"use strict";
// PoC Desk 화면 로직. 외부 라이브러리 없이 DOM API만 쓴다. 서버가 준 문자열은 항상 textContent 로 넣는다(XSS 방지).

const S = {
  tab: "ask", step: "ask", question: "", intake: null, metrics: [], metricKey: null, months: [],
  confirmed: false, mode: "reference", model: "", status: { llm_key: false, default_model: "", data_months: [] },
  run: null, report: null, loading: "", notice: "", draft: {},
};
const EXAMPLES = ["2월에 얼마 벌었어?", "1분기 환불률은?", "3월 취소율은 몇 %야?", "2월 주문 몇 건이야?", "재구매율은 얼마야?"];
const BADGE = { PASS: "통과", FLAG: "경고(불일치)", UNVERIFIED: "검증 불가", INFO: "기준 계산식", ERROR: "오류" };
const FIELDS = [["formula", "계산식", "예: 결제 완료 주문 중 두 번 이상 산 고객 수 ÷ 결제 고객 수"],
  ["include_exclude", "포함·제외", "예: 취소·환불 주문의 고객은 제외, 비회원은 제외"],
  ["period_basis", "기간 기준", "예: 결제일(paid_at) 기준"], ["unit", "단위", "예: %, 소수 둘째 자리 반올림"]];

// ---- 도우미
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
async function api(method, path, body) {
  const opt = { method, headers: { "X-PocDesk": "1" } };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}
const badge = (status, text) => h("span", { class: "badge " + status }, text || BADGE[status] || status);
function fmt(run) {
  if (run.value == null) return "-";
  if (run.unit === "%") return run.value.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "%";
  // 정수면 그대로, 소수가 있으면 둘째 자리까지 (정수로 반올림해서 값이 달라 보이지 않게)
  const text = Number.isInteger(run.value) ? run.value.toLocaleString("ko-KR")
    : run.value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
  return text + (run.unit || "");
}
const metricOf = (key) => S.metrics.find((m) => m.key === key);
const monthLabel = (m) => Number(m.slice(5)) + "월";

// ---- 공통 틀
function stepper() {
  const order = ["ask", "define", "result", "report"], names = ["1 접수", "2 정의 확인", "3 결과·대사", "4 리포트"];
  const now = order.indexOf(S.step);
  return h("ol", { class: "stepper", "aria-label": "진행 단계" },
    order.map((s, i) => h("li", { class: i < now ? "done" : i === now ? "now" : "", "aria-current": i === now ? "step" : null },
      (i < now ? "✓ " : "") + names[i])));
}
function renderStatus() {
  const el = document.getElementById("status");
  el.replaceChildren(
    h("span", { class: "badge neutral" }, "데이터 " + S.status.data_months.map(monthLabel).join("·")),
    h("span", { class: "badge " + (S.status.llm_key ? "on" : "off") }, S.status.llm_key ? "AI 모드 사용 가능" : "AI 키 없음 (계산식 모드만)"));
}
function render() {
  document.querySelectorAll(".tab").forEach((t) => {
    if (t.dataset.tab === S.tab) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
  });
  const view = document.getElementById("view");
  // 화면을 통째로 다시 그리면 키보드 포커스가 사라지므로, 조작하던 컨트롤(data-fk)을 기억했다가 되돌린다
  const active = document.activeElement;
  const focusKey = active && active.dataset ? active.dataset.fk : null;
  let node;
  if (S.tab === "dictionary") node = viewDictionary();
  else if (S.tab === "history") node = viewHistory();
  else node = { ask: viewAsk, define: viewDefine, result: viewResult, report: viewReport }[S.step]();
  view.replaceChildren(node);
  if (focusKey) {
    const again = view.querySelector(`[data-fk="${focusKey}"]`);
    if (again) again.focus();
  }
}
function go(step) { S.step = step; S.tab = "ask"; S.notice = ""; render(); document.getElementById("view").focus(); }

// ---- 1 접수 (S1)
function viewAsk() {
  const ta = h("textarea", { id: "q", placeholder: "예: 2월에 얼마 벌었어?", maxlength: 300, "aria-label": "질문" }, S.question);
  const submit = async () => {
    S.question = ta.value.trim();
    if (!S.question) { S.notice = "질문을 입력해 주세요."; return render(); }
    S.loading = "질문을 해석하는 중…"; render();
    try {
      S.intake = await api("POST", "/api/intake", { question: S.question });
      S.metricKey = S.intake.candidates.length ? S.intake.candidates[0].key : "__new";
      if (S.intake.candidates.length > 1) S.metricKey = "";
      S.months = S.intake.months.slice(); S.confirmed = false; S.draft = {}; S.notice = "";
      S.loading = ""; go("define");
    } catch (e) { S.loading = ""; S.notice = e.message; render(); }
  };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(); });
  return h("div", {}, stepper(),
    h("div", { class: "card" },
      h("h2", {}, "무엇이 궁금하세요?"),
      h("p", { class: "muted small" }, "한 줄로 물어보세요. 어떤 지표를 어떤 기준으로 계산할지 먼저 확인한 뒤에 계산합니다."),
      ta,
      S.notice && h("div", { class: "notice error", role: "alert" }, S.notice),
      h("div", { class: "row" }, h("button", { class: "btn", onclick: submit, disabled: !!S.loading }, S.loading ? "해석 중…" : "정의 확인하기"),
        h("span", { class: "muted small" }, "Ctrl + Enter로도 보낼 수 있어요")),
      h("div", { class: "chips" }, EXAMPLES.map((ex) => h("button", { class: "chip", onclick: () => { ta.value = ex; ta.focus(); } }, ex)))));
}

// ---- 2 정의 확인 (S2)
function viewDefine() {
  const it = S.intake;
  const isNew = S.metricKey === "__new";
  const metric = !isNew && S.metricKey ? metricOf(S.metricKey) : null;
  const modeAvailable = !!metric && (metric.has_reference || S.status.llm_key);
  const canRun = modeAvailable && S.months.length > 0 && S.confirmed;
  const parts = [stepper(), h("div", { class: "card" }, h("h2", {}, "질문"), h("p", {}, S.question))];

  // 지표 선택
  const sel = h("select", { id: "metric", "data-fk": "metric", "aria-label": "지표", onchange: (e) => { S.metricKey = e.target.value; S.confirmed = false; S.notice = ""; render(); } },
    h("option", { value: "" }, "— 지표를 고르세요 —"),
    S.metrics.map((m) => h("option", { value: m.key, selected: m.key === S.metricKey }, m.name + (m.source === "custom" ? " (사용자 정의)" : ""))),
    h("option", { value: "__new", selected: isNew }, "+ 새 지표 정의하기"));
  const why = it.status === "ok" ? "질문에서 이 지표를 찾았습니다. 맞는지 확인하세요."
    : it.status === "ambiguous" ? "여러 지표가 해석됩니다: " + it.candidates.map((c) => c.name).join(", ") + ". 하나를 골라 주세요."
    : "사전에 없는 지표 같습니다. 새로 정의해야 계산할 수 있습니다.";
  parts.push(h("div", { class: "card" }, h("h2", {}, "지표"), h("p", { class: "muted small" }, why), sel));

  // 기간
  const checks = S.status.data_months.map((m) => h("label", {},
    h("input", { type: "checkbox", "data-fk": "month-" + m, checked: S.months.includes(m), onchange: (e) => {
      S.months = e.target.checked ? [...S.months, m].sort() : S.months.filter((x) => x !== m); render(); } }), monthLabel(m)));
  parts.push(h("div", { class: "card" }, h("h2", {}, "기간"),
    it.invalid_months.length ? h("div", { class: "notice", role: "alert" }, "데이터가 없는 기간은 제외했습니다: " + it.invalid_months.map(monthLabel).join(", ") + " (데이터는 1~3월뿐입니다)") : null,
    it.period_missing && !S.months.length ? h("p", { class: "muted small" }, "질문에서 기간을 찾지 못했습니다. 직접 골라 주세요.") : null,
    h("div", { class: "checks-inline" }, checks)));

  // 정의
  if (metric) parts.push(definitionCard(metric)); else if (isNew) parts.push(newDefinitionForm());

  // 실행 방식
  if (metric) parts.push(runOptions(metric));
  if (S.notice) parts.push(h("div", { class: "notice error", role: "alert" }, S.notice));
  if (S.loading) parts.push(h("div", { class: "card", role: "status" }, h("span", { class: "spinner" }), " " + S.loading));
  parts.push(h("div", { class: "row" },
    h("button", { class: "btn secondary", onclick: () => go("ask") }, "← 다시 질문"),
    h("button", { class: "btn", disabled: !canRun || !!S.loading, onclick: execute }, "실행하기"),
    !canRun && metric ? h("span", { class: "muted small" },
      !modeAvailable ? "이 지표는 AI로만 계산할 수 있는데 API 키가 없습니다. 키를 설정하고 앱을 다시 시작하세요."
        : !S.months.length ? "기간을 하나 이상 고르세요" : "정의를 확인했다고 체크하세요") : null));
  return h("div", {}, parts);
}
function definitionCard(m) {
  const confirm = h("input", { type: "checkbox", id: "confirm", "data-fk": "confirm", checked: S.confirmed, onchange: (e) => { S.confirmed = e.target.checked; render(); } });
  return h("div", { class: "card" },
    h("div", { class: "row" }, h("h2", {}, m.name + " 정의"), badge("neutral", m.version)),
    h("dl", { class: "def" },
      FIELDS.flatMap(([k, label]) => [h("dt", {}, label), h("dd", {}, m[k])]),
      m.aliases.length ? [h("dt", {}, "다른 말"), h("dd", {}, m.aliases.join(", "))] : []),
    h("label", { for: "confirm", class: "row" }, confirm, "이 정의로 계산하는 것이 맞습니다 (확정)"));
}
function newDefinitionForm() {
  const d = S.draft;
  const filled = () => ["name", ...FIELDS.map((f) => f[0])].filter((k) => (d[k] || "").trim()).length;
  const prog = h("span", {}), txt = h("p", { class: "small muted" });
  const saveBtn = h("button", { class: "btn", type: "button" }, "정의 저장하고 확정");
  const refresh = () => {
    const n = ["formula", "include_exclude", "period_basis", "unit"].filter((k) => (d[k] || "").trim()).length;
    prog.style.width = (n / 4 * 100) + "%";
    txt.textContent = `필수 4항목 중 ${n}개 작성` + ((d.name || "").trim() ? "" : " · 지표 이름 필요");
    saveBtn.disabled = !(n === 4 && (d.name || "").trim());
  };
  const field = (key, label, ph, multi) => h("div", {}, h("label", { for: "f-" + key }, label),
    h(multi ? "textarea" : "input", { id: "f-" + key, type: multi ? null : "text", placeholder: ph, value: multi ? null : d[key] || "",
      oninput: (e) => { d[key] = e.target.value; refresh(); } }, multi ? d[key] || "" : null));
  saveBtn.addEventListener("click", async () => {
    try {
      const m = await api("POST", "/api/definition", { name: d.name, aliases: d.aliases || "", formula: d.formula,
        include_exclude: d.include_exclude, period_basis: d.period_basis, unit: d.unit });
      S.metrics = (await api("GET", "/api/dictionary")).metrics; S.metricKey = m.key; S.confirmed = true; S.draft = {};
      S.notice = ""; render();
    } catch (e) { S.notice = e.message; render(); }
  });
  const box = h("div", { class: "card" }, h("h2", {}, "새 지표 정의"),
    h("p", { class: "muted small" }, "계산하기 전에 네 가지를 먼저 정해야 합니다. 정의가 없으면 AI가 추측해서 틀린 숫자를 낼 수 있기 때문입니다."),
    h("div", { class: "progress" }, prog), txt,
    field("name", "지표 이름", "예: 재구매율"), field("aliases", "다른 말 (쉼표로 구분, 선택)", "예: 재구매 비율, 리피트율"),
    FIELDS.map(([k, label, ph]) => field(k, label, ph, true)), h("div", { class: "row" }, saveBtn));
  refresh();
  return box;
}
function runOptions(m) {
  const radio = (value, label, hint, disabled) => h("label", { class: "row" },
    h("input", { type: "radio", name: "mode", "data-fk": "mode-" + value, value, checked: S.mode === value, disabled, onchange: () => { S.mode = value; render(); } }),
    h("span", {}, label, h("span", { class: "muted small" }, " — " + hint)));
  const aiOff = !S.status.llm_key;
  if (!m.has_reference && S.mode === "reference") S.mode = "ai";
  if (aiOff && m.has_reference) S.mode = "reference";  // 키가 없으면 AI 방식이 선택된 채로 남지 않게
  return h("div", { class: "card" }, h("h2", {}, "실행 방식"),
    radio("reference", "검증된 계산식으로 실행", m.has_reference ? "사람이 검토한 SQL, 즉시 계산" : "이 지표에는 없음", !m.has_reference),
    radio("ai", "AI가 SQL 생성", aiOff ? "API 키가 없어 사용할 수 없음 (앱을 시작한 창에서 NVIDIA_API_KEY 설정)" : "수십 초에서 몇 분 걸릴 수 있음, 결과는 기준값과 대조", aiOff),
    S.mode === "ai" && !aiOff ? [h("label", { for: "model" }, "모델"),
      h("input", { id: "model", type: "text", value: S.model || S.status.default_model, oninput: (e) => { S.model = e.target.value; } })] : null);
}
async function execute() {
  S.loading = S.mode === "ai" ? "AI가 SQL을 만들고 있습니다… 최대 몇 분 걸릴 수 있어요. 창을 닫지 마세요." : "계산하는 중…";
  S.notice = ""; render();
  try {
    S.run = await api("POST", "/api/run", { question: S.question, metric_key: S.metricKey, months: S.months,
      mode: S.mode, model: S.model || S.status.default_model });
    S.loading = ""; S.report = null; go("result");
  } catch (e) { S.loading = ""; S.notice = e.message; render(); }
}

// ---- 3 결과·대사 (S3, S4)
function viewResult() {
  const r = S.run;
  const head = h("div", { class: "card" }, h("div", { class: "row" }, h("h2", {}, r.metric_name), badge(r.overall)),
    h("p", { class: "muted small" }, `${r.months.map(monthLabel).join(", ")} · ${r.mode === "reference" ? "검증된 계산식" : "AI(" + r.model + ")"} · 정의 ${r.definition_version} · ${r.elapsed_ms}ms`));
  const body = [];
  if (r.status !== "OK" || r.overall === "ERROR") {
    body.push(h("div", { class: "notice error", role: "alert" }, h("strong", {}, "실행하지 못했습니다. "), r.error || "알 수 없는 오류",
      h("p", { class: "small" }, r.mode === "ai" ? "AI 모드의 오류는 기록에 남았습니다. 검증된 계산식 방식으로 다시 시도해 볼 수 있습니다." : "기록에 남았습니다.")));
  } else {
    head.append(h("div", { class: "bigvalue", "aria-label": "결과 값" }, fmt(r)));
  }
  body.push(head);
  if (r.checks.length) body.push(h("div", { class: "card" }, h("h2", {}, "정답과 대조 (대사)"),
    r.checks.map((c) => h("div", { class: "check" }, badge(c.status), h("div", {}, h("strong", {}, c.name), h("div", { class: "small" }, c.message))))));
  if (r.overall === "UNVERIFIED") body.push(h("div", { class: "notice info" }, "정답 출처가 없는 지표는 어떤 방법으로도 '검증됨'을 붙일 수 없습니다(실험 결과, docs/07 7절). 이 숫자는 사람이 확인해야 합니다."));
  if (r.overall === "FLAG") body.push(h("div", { class: "notice" }, "정답과 다릅니다. 이 숫자를 보고서에 쓰지 마세요. 거의 맞아 보이는 숫자도 틀릴 수 있습니다."));
  if (r.sql) body.push(h("div", { class: "card" }, h("details", {}, h("summary", {}, "실행한 SQL 보기"), h("pre", {}, r.sql))));
  body.push(h("div", { class: "row" },
    h("button", { class: "btn secondary", onclick: () => { S.question = ""; go("ask"); } }, "새 질문"),
    h("button", { class: "btn", onclick: makeReport, disabled: r.status !== "OK" && !r.error }, "리포트 만들기")));
  return h("div", {}, stepper(), body, S.notice && h("div", { class: "notice error", role: "alert" }, S.notice));
}
async function makeReport() {
  try { S.report = await api("POST", "/api/report", { run_id: S.run.id }); go("report"); }
  catch (e) { S.notice = e.message; render(); }
}

// ---- 4 리포트 (S5)
function viewReport() {
  const rp = S.report;
  const blob = new Blob([rp.markdown], { type: "text/markdown;charset=utf-8" });
  if (S.reportUrl) URL.revokeObjectURL(S.reportUrl);  // 화면을 다시 그릴 때마다 쌓이지 않게 이전 주소를 해제
  S.reportUrl = URL.createObjectURL(blob);
  const link = h("a", { class: "btn", href: S.reportUrl, download: rp.filename }, "md 파일 내려받기");
  return h("div", {}, stepper(), h("div", { class: "card" }, h("h2", {}, "리포트가 저장되었습니다"),
    h("p", { class: "small muted" }, "서버 PC에 저장된 위치: " + rp.path), h("div", { class: "row" }, link,
      h("button", { class: "btn secondary", onclick: () => go("result") }, "← 결과로"),
      h("button", { class: "btn secondary", onclick: () => { S.question = ""; go("ask"); } }, "새 질문")),
    h("pre", {}, rp.markdown)));
}

// ---- KPI 사전 (S6)
function viewDictionary() {
  return h("div", {}, h("div", { class: "card" }, h("h2", {}, "KPI 사전"),
    h("p", { class: "muted small" }, "지표마다 계산식, 포함·제외, 기간 기준, 단위를 한 곳에 확정한 것입니다. 사용자가 정의한 지표는 수정하면 버전이 올라갑니다.")),
    S.metrics.map((m) => h("div", { class: "card" }, h("div", { class: "row" }, h("h3", {}, m.name), badge("neutral", m.version),
      badge(m.has_reference ? "INFO" : "UNVERIFIED", m.has_reference ? "기준 계산식 있음" : "기준 계산식 없음")),
      h("dl", { class: "def" }, FIELDS.flatMap(([k, label]) => [h("dt", {}, label), h("dd", {}, m[k])]),
        m.aliases.length ? [h("dt", {}, "다른 말"), h("dd", {}, m.aliases.join(", "))] : []),
      m.history && m.history.length ? h("details", {}, h("summary", {}, `이전 버전 ${m.history.length}개`),
        m.history.map((o) => h("pre", {}, `v${o.version} (${o.created_at})\n계산식: ${o.formula}\n포함·제외: ${o.include_exclude}\n기간 기준: ${o.period_basis}\n단위: ${o.unit}`))) : null)));
}

// ---- 기록 (F9)
function viewHistory() {
  const rows = S.historyRows;
  if (!rows) { loadHistory(); return h("div", { class: "card empty" }, h("span", { class: "spinner" }), " 불러오는 중…"); }
  if (!rows.length) return h("div", { class: "card empty" }, "아직 실행 기록이 없습니다. 질문하기에서 첫 질문을 해 보세요.");
  return h("div", { class: "card" }, h("h2", {}, "최근 실행 기록"), h("div", { class: "tablewrap" },
    h("table", {}, h("thead", {}, h("tr", {}, ["시각", "질문", "지표", "방식", "결과", "판정"].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows.map((r) => h("tr", { class: "click", tabindex: 0, onclick: () => openRun(r), onkeydown: (e) => { if (e.key === "Enter") openRun(r); } },
        h("td", {}, r.ts.replace("T", " ").slice(5, 16)), h("td", {}, r.question), h("td", {}, r.metric_name),
        h("td", {}, r.mode === "ai" ? "AI" : "계산식"), h("td", {}, r.value == null ? "-" : fmt(r)), h("td", {}, badge(r.overall))))))));
}
async function loadHistory() {
  try { S.historyRows = (await api("GET", "/api/history")).runs; } catch { S.historyRows = []; }
  if (S.tab === "history") render();
}
function openRun(r) { S.run = r; S.report = null; go("result"); }

// ---- 시작
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  S.tab = t.dataset.tab; if (S.tab === "history") S.historyRows = null; render();
}));
(async function init() {
  try {
    S.status = await api("GET", "/api/status"); S.metrics = (await api("GET", "/api/dictionary")).metrics;
    S.model = S.status.default_model;
  } catch (e) { S.notice = "서버에 연결하지 못했습니다: " + e.message; }
  renderStatus(); render();
})();
