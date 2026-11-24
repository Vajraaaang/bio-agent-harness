"""Offline MCP Apps resources for plan, run and receipt readback."""

from html import escape
from typing import Any

MIME = "text/html;profile=mcp-app"
RESOURCE_META: dict[str, Any] = {
    "ui": {"csp": {"connectDomains": [], "resourceDomains": []}}
}
CARD_NAMES = {
    "plan-card": "Immutable RNA plan",
    "run-card": "RNA workflow progress",
    "receipt-card": "RNA execution receipt",
}

_SCRIPT = r"""
const kind = document.body.dataset.card;
const body = document.querySelector('main');
const footer = document.querySelector('footer');
const calls = new Map();
let nextId = 0;
let run = null;
let hostOrigin;
let hostTheme;
function applyHostTheme(context) {
  if (context?.theme === "light" || context?.theme === "dark") {
    hostTheme = context.theme;
    document.documentElement.style.colorScheme = hostTheme;
  }
}
function send(method, params, reply) {
  if (window.parent === window) return null;
  const request = {jsonrpc: "2.0", method, params};
  if (reply) {
    request.id = ++nextId;
    calls.set(request.id, reply);
  }
  window.parent.postMessage(request, hostOrigin || "*");
  return request.id ?? null;
}
function text(tag, value, at = body) {
  const element = document.createElement(tag);
  element.textContent = String(value);
  at.append(element);
  return element;
}
function facts(entries, at = body) {
  const dl = document.createElement("dl");
  at.append(dl);
  for (const [name, value] of entries) {
    if (value === null || value === undefined || value === "") continue;
    text("dt", name, dl);
    text("dd", value, dl);
  }
}
function detail(title, rows) {
  if (!rows?.length) return;
  const panel = document.createElement("details");
  text("summary", title, panel);
  for (const row of rows) {
    facts(Object.entries(row).map(([key,value]) => [
      key.replaceAll("_"," "), typeof value === "object" ? JSON.stringify(value) : value
    ]), panel);
  }
  body.append(panel);
  return panel;
}
function studio(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "http:" && url.hostname === "127.0.0.1" && url.port
        && !url.username && !url.password && url.pathname === "/" && !url.hash
        && url.searchParams.size === 2
        && /^[a-f0-9]{32}$/.test(url.searchParams.get("session_id") || "")
        && /^(overview|findings|workflows|qc|figures|code|evidence)$/.test(
            url.searchParams.get("view") || "")) return url.href;
  } catch {}
  return null;
}
function toolResult(result) {
  if (!result || typeof result !== "object") return;
  if (result.structuredContent) return render(result.structuredContent);
  const item = result.content?.find(item => item.type === "text");
  if (!item) return;
  try { render(JSON.parse(item.text)); } catch {}
}
function button(label, handler) {
  const node = text("button", label, footer);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
function studioHandoff(url) {
  const studioAddress = () => hostTheme ? url + "#studio-theme=" + hostTheme : url;
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  const fallback = document.createElement("details");
  text("summary", "Local Studio address", fallback);
  const field = document.createElement("input");
  field.type = "text";
  field.readOnly = true;
  field.value = studioAddress();
  field.setAttribute("aria-label", "Local Studio address");
  field.addEventListener("focus", () => field.select());
  fallback.append(field);
  const showAddress = () => {
    field.value = studioAddress();
    fallback.open = true;
    status.textContent = "Copy the local address and open it in your browser.";
  };
  const open = button("Open in Research Studio", () => {
    if (open.disabled) return;
    open.disabled = true;
    status.textContent = "Asking the app to open Companion Studio…";
    const finish = failed => {
      open.disabled = false;
      if (failed) showAddress();
      else status.textContent = "Opening Companion Studio in your browser.";
    };
    let timeout;
    const id = send("ui/open-link", {url:studioAddress()}, response => {
      clearTimeout(timeout);
      finish(Boolean(response.error || !response.result || response.result.isError));
    });
    if (id === null) {
      finish(true);
      return;
    }
    timeout = setTimeout(() => {
      calls.delete(id);
      finish(true);
    }, 2000);
  });
  footer.append(status, fallback);
}
function renderPlan(data) {
  const runtime = data.runtime || {};
  const answer = data.answerability || {};
  const design = answer.local_design || {};
  facts([
    ["Plan ID", data.plan_id], ["SHA-256", data.plan_sha256],
    ["Pinned OCI digest", runtime.oci_digest], ["Architecture", runtime.architecture],
    ["Runtime verification", runtime.oci_runtime_verification],
    ["Runtime lock SHA-256", runtime.lockfile_sha256],
    ["Network policy", runtime.network_policy],
    ["Answerability", answer.status],
    ["Registry families", answer.registered_family_count],
    ["Local design", design.status],
    ["Declared biological units", design.declared_units],
    ["Design rank",
     design.rank !== undefined ? design.rank + " / " + design.columns : null],
    ["Residual degrees of freedom", design.residual_df],
    ["Caller design evidence", answer.design_evidence_status],
    ["Reason", design.reason || answer.reason],
  ]);
  detail("Inputs", data.inputs);
  detail("Steps", data.steps);
  detail("Supplied references", data.references);
}
function renderRun(data) {
  if (data.run_id && data.session_id) {
    run = {run_id:data.run_id, session_id:data.session_id};
  }
  facts([
    ["Run ID", data.run_id], ["Compute target", data.compute_target],
    ["Status", data.status],
    ["Generation", data.generation], ["Stale", data.stale],
  ]);
  if (data.progress?.total > 0) {
    text("p", data.progress.completed + " / " + data.progress.total + " complete");
    const bar = document.createElement("progress");
    bar.max = data.progress.total;
    bar.value = data.progress.completed;
    bar.setAttribute("aria-label", "Completed tasks");
    body.append(bar);
  }
  if (data.continuation) text("p", data.continuation);
  if (data.tasks?.length) {
    text("h2", "Task attempts");
    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    for (const title of ["Step", "Target", "Duration", "Status"]) {
      text("th", title, head);
    }
    const rows = table.createTBody();
    for (const task of data.tasks) {
      const row = rows.insertRow();
      text("td", task.step, row);
      text("td", task.target, row);
      text("td", task.duration_seconds != null ? task.duration_seconds + " s"
         : task.duration || "Pending", row);
      text("td", task.status, row);
    }
    body.append(table);
  }
  for (const warning of data.warnings || []) text("p", warning).className = "warning";
  if (run) button("Refresh", () => send("tools/call",
    {name:"get_run_status", arguments:run}, response => toolResult(response.result)));
}
function numericTables(tables) {
  for (const entry of (tables || []).slice(0, 64)) {
    if (!entry.rows?.length || !entry.columns?.length) continue;
    const table = document.createElement("table");
    text("caption", entry.caption, table);
    const headings = table.createTHead().insertRow();
    for (const column of entry.columns) {
      text("th", column, headings).scope = "col";
    }
    const rows = table.createTBody();
    for (const values of entry.rows.slice(0, 20)) {
      const row = rows.insertRow();
      for (const value of values) text("td", value ?? "—", row);
    }
    body.append(table);
  }
}
function renderReceipt(data) {
  facts([
    ["Run ID", data.run_id], ["Workflow", data.operation], ["Status", data.status],
    ["Revision", data.revision], ["Package root SHA-256", data.package_root_sha256],
    ["0 ≤ p ≤ q ≤ 1 check", data.probability_bounds_status],
    ["Replay bundle SHA-256", data.replay_bundle_sha256],
  ]);
  if (data.metrics) {
    const grid = document.createElement("div");
    grid.className = "metrics";
    for (const [key, value] of Object.entries(data.metrics)) {
      if (!["string", "number", "boolean"].includes(typeof value)) continue;
      const tile = document.createElement("section");
      text("span", key.replaceAll("_", " "), tile);
      text("strong", value, tile);
      grid.append(tile);
    }
    body.append(grid);
  }
  numericTables(data.summary_tables);
  if (data.aims) {
    if (!data.aims.length) text("p", "No research aims recorded.");
    else detail("Research aims", data.aims).open = true;
  }
  const preview = data.figure_preview;
  if (typeof preview === "string" && preview.length <= 524288
      && /^data:image\/png;base64,[a-z0-9+/]+=*$/i.test(preview)) {
    const figure = document.createElement("figure");
    const img = document.createElement("img");
    img.alt = "Verified RNA analysis preview";
    img.src = preview;
    figure.append(img);
    body.append(figure);
  }
  for (const limitation of data.limitations || []) text("p", limitation);
  detail("Tool receipts", data.tool_receipts);
}
function render(data) {
  body.replaceChildren();
  footer.replaceChildren();
  const studioUrl = studio(data.studio_url);
  const localPlan = data.status === "local_studio_only" && data.plan_id;
  if (data.error) {
    const reason = data.reason || "operation_unavailable";
    text("p", "OperationBlocked: " + reason).className = "warning";
  } else if (localPlan) {
    if (data.execution_blocker === "private_plan_execution_unavailable") {
      text("p", "This private plan has no supported execution route. "
        + "Review its details in offline Companion Studio.").className = "warning";
    } else if (data.execution_route === "offline_studio") {
      text("p", "Review and approve the local run in offline Companion Studio.");
    } else {
      text("p", "Review the private plan in offline Companion Studio.");
    }
    facts([["Plan ID", data.plan_id], ["SHA-256", data.plan_sha256]]);
  } else if (kind === "plan-card") renderPlan(data);
  else if (kind === "run-card") renderRun(data);
  else renderReceipt(data);
  if (studioUrl) studioHandoff(studioUrl);
  if (data.launch_hint && (!studioUrl || data.status === "awaiting_review")) {
    text("code", data.launch_hint, footer);
  }
  if (kind === "plan-card" && !localPlan) text("p",
    "Read-only · This view cannot authorize or start execution. Next, the agent calls "
    + "execute_reviewed_plan with this Plan ID and SHA-256; Codex presents one native "
    + "approval.", footer);
  if (kind === "receipt-card") text("p", "Clinical release disabled", footer);
}
window.addEventListener("message", event => {
  if (event.source !== window.parent || (hostOrigin && event.origin !== hostOrigin)) {
    return;
  }
  const message = event.data;
  if (!message || message.jsonrpc !== "2.0") return;
  if (message.method === "ui/notifications/tool-result") {
    toolResult(message.params);
    return;
  }
  if (message.method === "ui/notifications/host-context-changed") {
    applyHostTheme(message.params);
    return;
  }
  const reply = calls.get(message.id);
  if (reply) {
    hostOrigin = event.origin === "null" ? undefined : event.origin;
    calls.delete(message.id);
    reply(message);
  }
});
render({});
send("ui/initialize", {
  appInfo: {name:"DevHarness RNA Research", version:"0.2.0"},
  appCapabilities:{}, protocolVersion:"2026-01-26"
}, response => {
  if (response.result) {
    applyHostTheme(response.result.hostContext);
    send("ui/notifications/initialized", {});
  }
});
"""

_CSS = """
:root{color-scheme:light dark;font:14px system-ui,sans-serif;color:CanvasText;
background:Canvas}body{margin:0;padding:1.1rem}h1{font-size:1.25rem;margin:0 0 .9rem}
h2{font-size:1rem}dl{display:grid;grid-template-columns:minmax(7.5rem,auto) 1fr;
gap:.4rem .8rem}dd{margin:0;overflow-wrap:anywhere}dt,span{opacity:.72}
details,footer,figure,.metrics section{
border:1px solid color-mix(in srgb,CanvasText 22%,Canvas);
padding:.75rem;margin:.65rem 0;border-radius:.5rem}
footer p{margin:.45rem 0}button{padding:.4rem .68rem;margin:0 .5rem .3rem 0}
code{display:block;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}
footer input{box-sizing:border-box;width:100%;font:inherit;margin-top:.5rem}
caption{text-align:left;font-weight:600;padding:.65rem 0 .25rem}
th,td{padding:.35rem;text-align:left;border-bottom:1px solid GrayText;
overflow-wrap:anywhere}
progress{width:100%;accent-color:GrayText}
.warning{border-inline-start:.2rem solid #b66e43;
padding:.55rem}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(8rem,1fr));
gap:.55rem}.metrics strong{display:block;margin-top:.3rem}figure{background:Canvas}
img{max-width:100%;display:block;margin:auto}
"""


def card_html(card: str) -> str:
    title = escape(CARD_NAMES[card])
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src data:; connect-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        f"<title>{title}</title><style>{_CSS}</style></head>"
        f'<body data-card="{escape(card)}"><h1>{title}</h1>'
        f'<main aria-live="polite"></main><footer></footer><script>{_SCRIPT}</script>'
        "</body></html>"
    )
