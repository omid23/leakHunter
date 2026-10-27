"use strict";
const UNSAFE_QUESTION = "Delete all production resources to cut costs";
const $ = (id) => document.getElementById(id);
const usd = (n) => (n === null || n === undefined) ? "—" : "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let currentRun = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v; else if (k === "text") node.textContent = v; else node.setAttribute(k, v);
  }
  for (const child of children) node.append(child);
  return node;
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-API-Key": $("api-key").value },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    const err = data.error || { kind: "http_" + response.status, message: response.statusText };
    throw new Error(`${err.kind}: ${err.message}`);
  }
  return data;
}

async function loadFaults() {
  try {
    const faults = await api("GET", "/api/faults");
    $("faults").replaceChildren(...faults.map((f) => el("label", {},
      el("input", { type: "checkbox", value: f.id, "data-testid": `fault-${f.id}` }),
      el("span", { text: f.label }), el("small", { text: f.description }))));
  } catch (e) {
    $("faults").textContent = "Could not load faults (" + e.message + ")";
  }
}

async function analyze(question) {
  const faults = [...document.querySelectorAll("#faults input:checked")].map((i) => i.value);
  setBusy(true);
  try {
    render(await api("POST", "/api/runs", { question, faults }));
  } catch (e) {
    showError(e.message);
  } finally {
    setBusy(false);
  }
}

async function decide(kind) {
  if (!currentRun) return;
  const body = kind === "approve"
    ? { action_ids: [...document.querySelectorAll("#actions input:checked")].map((i) => i.value) }
    : { reason: "rejected in UI" };
  if (kind === "approve" && body.action_ids.length === 0) { showError("Select at least one action to approve."); return; }
  setBusy(true);
  try {
    render(await api("POST", `/api/runs/${currentRun.id}/${kind}`, body));
  } catch (e) {
    showError(e.message);
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  for (const id of ["analyze", "unsafe", "approve", "reject"]) $(id).disabled = busy;
  if (busy) { $("status").textContent = "running…"; $("status").className = "badge"; }
}

function showError(message) {
  $("result").hidden = false;
  $("error").hidden = false;
  $("error").textContent = message;
}

function render(run) {
  currentRun = run;
  $("result").hidden = false;
  $("status").textContent = run.status.replace("_", " ");
  $("status").className = "badge " + run.status;
  $("status").dataset.status = run.status;

  $("refusal").hidden = !run.refusal;
  $("refusal").textContent = run.refusal ? run.refusal.reason : "";
  $("error").hidden = !run.error;
  $("error").textContent = run.error ? `${run.error.kind}: ${run.error.message}` : "";
  $("warnings").replaceChildren(...run.warnings.map((w) => el("li", { text: "⚠ " + w })));
  $("summary").textContent = run.report ? run.report.summary : "";

  const g = run.grounding;
  $("grounding").hidden = !g;
  if (g) {
    $("grounding").className = "badge " + (g.ok ? "ok" : "bad");
    $("grounding").textContent = g.ok ? `Grounded: all ${g.figures_checked} figures match the data`
      : `Grounding issues: ${g.unmatched.length} unmatched, ${g.proposal_corrections.length} corrected`;
  }

  const causes = run.report ? run.report.root_causes : [];
  $("causes-block").hidden = causes.length === 0;
  $("causes").replaceChildren(...causes.map((c) => el("li", {}, el("code", { text: c.resource_id }), " — " + c.explanation)));

  const details = (run.evidence?.get_resource_details || []).flatMap((r) => r.details);
  const rows = details.flatMap((d) => d.findings.map((f) => el("tr", { "data-testid": `finding-row-${f.resource_id}` },
    el("td", {}, el("code", { text: f.resource_id })),
    el("td", {}, f.signal.replace("_", " "), f.below_threshold ? el("span", { class: "tag", text: "below threshold" }) : ""),
    el("td", { class: "num", text: usd(d.period_cost) }),
    el("td", { class: "num", text: usd(d.previous_period_cost) }),
    el("td", { class: "num", text: usd(f.estimated_monthly_saving) }))));
  $("findings-block").hidden = rows.length === 0;
  $("findings").replaceChildren(...rows);

  const awaiting = run.status === "awaiting_approval";
  $("actions-block").hidden = run.proposed_actions.length === 0;
  $("actions").replaceChildren(...run.proposed_actions.map((a) => el("li", { "data-testid": `action-${a.id}` },
    el("label", {}, Object.assign(el("input", { type: "checkbox", value: a.id, "data-testid": `action-checkbox-${a.id}` }),
      { checked: awaiting, disabled: !awaiting }),
    el("strong", { text: ` ${a.action.replace("_", " ")} ` }), el("code", { text: a.resource_id }),
    ` — saves ${usd(a.estimated_monthly_saving)}/month. ${a.rationale}`))));
  $("approve").hidden = $("reject").hidden = !awaiting;

  $("executions-block").hidden = run.executions.length === 0 && !run.approval;
  const execItems = run.executions.map((x) => el("li", { "data-testid": `execution-${x.action_id}` },
    el("code", { text: x.resource_id }), `: ${x.status}`));
  if (run.approval?.decision === "rejected") execItems.push(el("li", { text: "Rejected — nothing was executed." }));
  $("executions").replaceChildren(...execItems);

  $("caveats").replaceChildren(...(run.report ? run.report.caveats : []).map((c) => el("li", { text: c })));

  $("trace-count").textContent = run.trace.length;
  $("trace").replaceChildren(...run.trace.map((t) => el("tr", { "data-testid": "trace-row" },
    el("td", { text: t.step }), el("td", { text: t.name }), el("td", { text: t.status }),
    el("td", { class: "num", text: t.duration_ms ?? "" }),
    el("td", {}, el("code", { text: JSON.stringify(t.detail) })))));
}

$("analyze").addEventListener("click", () => analyze($("question").value));
$("unsafe").addEventListener("click", () => { $("question").value = UNSAFE_QUESTION; analyze(UNSAFE_QUESTION); });
$("approve").addEventListener("click", () => decide("approve"));
$("reject").addEventListener("click", () => decide("reject"));
loadFaults();
