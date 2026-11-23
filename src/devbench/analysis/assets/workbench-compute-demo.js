"use strict";

const byId = (id) => document.getElementById(id);
const text = (id, value) => { byId(id).textContent = value; };
const show = (id, visible) => { byId(id).hidden = !visible; };
const value = (number) => number === null || number === undefined ? "Unavailable" : String(number);
const bytes = (number) => `${number.toLocaleString("en-US")} bytes`;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let state;
let busy = false;
let revision = 0;

function render(next) {
  state = next;
  const {connection, usage, review} = state;
  const remote = state.backend === "colab";
  const connected = ["Connected", "Stale"].includes(connection.state);
  const connecting = connection.state === "Connecting";
  const backend = remote ? "Google Colab" : "This Mac";
  for (const radio of document.querySelectorAll('input[name="backend"]')) {
    radio.checked = radio.value === state.backend;
  }
  byId("fixture").value = state.fixture;
  show("inline-connect", !connected && !connecting);
  show("connect", !connected && !connecting);
  show("disconnect", connected || connecting || connection.state === "Error");
  show("reconnect", connected || connection.state === "Error");
  byId("refresh").disabled = !connected;
  text("backend-summary", backend);
  text("input-summary", bytes(review.total_bytes));
  text("connection-state", connection.state);
  text("account-label", connection.account_label ?? "Unavailable");
  text("account-id", connection.masked_account_id ?? "Unavailable");
  text("generation", connection.connection_generation);
  text("remaining", value(usage?.remaining_cu));
  text("rate", value(usage?.aggregate_cu_per_hour));
  text("runtimes", value(usage?.active_runtimes));
  const freshness = usage?.is_stale ? state.usage_label : "Last checked";
  text("usage-freshness", usage ? `${freshness} · ${usage.checked_at_utc}` : "Usage unavailable");
  byId("usage-freshness").classList.toggle("stale", Boolean(usage?.is_stale));

  text("review-backend", backend);
  show("remote-review", remote);
  text("review-account", connection.account_label ?? "Unavailable");
  text("review-generation", connection.connection_generation);
  text("review-usage", usage ? `${state.usage_label} · ${value(usage.aggregate_cu_per_hour)} CU/h · ${value(usage.active_runtimes)} active` : "Unavailable");
  const profile = review.profile;
  text("profile", `${profile.hardware.toUpperCase()} · ${profile.min_ram_gb} GB RAM · ${profile.min_disk_gb} GB disk · ${profile.deadline_seconds / 60} min`);
  byId("file-list").replaceChildren(...review.files.map((file) => {
    const item = document.createElement("li");
    item.textContent = `${file.name} · ${bytes(file.size_bytes)}`;
    return item;
  }));
  text("transfer-summary", remote
    ? `${bytes(review.total_bytes)} total · Limits ${bytes(review.per_file_limit)} per file / ${bytes(review.total_limit)} per run`
    : `${bytes(review.total_bytes)} total · Local input`);
  show("transfer-warning", remote && Boolean(review.transfer_warning));
  text("transfer-warning", "The selected input exceeds the direct-upload limit.");
  text("blocked", review.blocked_message ?? "");
  byId("approve").disabled = !review.can_run;
  show("test-guard", review.can_test_guard);
  if (state.last_result) {
    show("result", true);
    text("result", `This Mac · Synthetic QC complete · Library sums ${state.last_result.library_sums.join(", ")}`);
  }
}

async function request(action, params = {}) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action, ...params}),
  });
  const body = await response.json();
  if (!response.ok) {
    if (body.state) render(body.state);
    throw new Error(body.error?.code ? `${body.error.code} · ${body.error.message}` : "The demo request failed.");
  }
  render(body);
}

async function act(action, params) {
  if (busy) return;
  busy = true;
  revision += 1;
  show("app-error", false);
  show("run-feedback", false);
  try {
    await request(action, params);
    if (action === "connect" || action === "reconnect") {
      await delay(300);
      await request("finish_connection");
    }
    if (action === "run") byId("run-review").close();
  } catch (error) {
    const destination = byId("run-review").open ? "run-feedback" : "app-error";
    text(destination, error.message);
    show(destination, true);
  } finally {
    busy = false;
  }
}

async function readState() {
  const version = revision;
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error("The demo state is unavailable.");
    const next = await response.json();
    if (!busy && version === revision) render(next);
  } catch (error) {
    text("app-error", error.message);
    show("app-error", true);
  }
}

for (const tab of document.querySelectorAll("[data-view]")) {
  tab.addEventListener("click", () => {
    for (const button of document.querySelectorAll("[data-view]")) button.removeAttribute("aria-current");
    tab.setAttribute("aria-current", "page");
    for (const view of ["methods", "settings"]) show(view, view === tab.dataset.view);
  });
}
for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => act(button.dataset.action));
}
for (const input of document.querySelectorAll('input[name="backend"]')) {
  input.addEventListener("change", () => act("select_backend", {backend: input.value}));
}
byId("fixture").addEventListener("change", (event) => act("select_fixture", {fixture: event.target.value}));
byId("open-review").addEventListener("click", () => {
  if (!state) return;
  show("run-feedback", false);
  byId("run-review").showModal();
});

readState();
setInterval(() => { if (!busy) readState(); }, 1000);
