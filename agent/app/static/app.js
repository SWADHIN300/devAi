const $ = (selector) => document.querySelector(selector);
const state = { runs: [], selectedId: null, previewId: null, previewPath: null };

function text(node, value) { node.textContent = value ?? ""; }
function dateLabel(value) {
  if (!value) return "just now";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}
function errorMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => `${item.loc?.slice(-1)[0] || "request"}: ${item.msg}`).join(" · ");
  return fallback;
}
async function request(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...options.headers } });
  let payload = {};
  try { payload = await response.json(); } catch { /* A non-JSON server error gets a useful fallback. */ }
  if (!response.ok) throw new Error(errorMessage(payload, `Request failed (${response.status})`));
  return payload;
}
function showNotice(message, success = false, target = "#global-notice") {
  const node = $(target);
  text(node, message);
  node.classList.toggle("success", success);
  node.hidden = false;
}
function clearNotice(target = "#global-notice") { $(target).hidden = true; }
function selectedRun() { return state.runs.find((run) => run.id === state.selectedId); }

async function refreshRuns(keepSelection = true) {
  try {
    const data = await request("/v1/tasks?limit=100");
    state.runs = data.tasks || [];
    if (!keepSelection || !state.runs.some((run) => run.id === state.selectedId)) {
      state.selectedId = state.runs[0]?.id || null;
    }
    renderRuns();
    if (state.selectedId) await loadTask(state.selectedId);
    else renderNoTask();
  } catch (error) {
    const list = $("#run-list");
    list.replaceChildren();
    const message = document.createElement("div");
    message.className = "rail-empty";
    message.textContent = `History unavailable: ${error.message}`;
    list.append(message);
    setApiState(false);
  }
}
function renderRuns() {
  const list = $("#run-list");
  list.replaceChildren();
  if (!state.runs.length) {
    const empty = document.createElement("div");
    empty.className = "rail-empty";
    empty.textContent = "No task runs yet. Start with a request above.";
    list.append(empty);
    return;
  }
  for (const run of state.runs) {
    const button = document.createElement("button");
    button.className = `run-item${run.id === state.selectedId ? " selected" : ""}`;
    button.dataset.status = run.status;
    button.type = "button";
    button.setAttribute("aria-current", run.id === state.selectedId ? "true" : "false");
    const line = document.createElement("span");
    line.className = "run-line";
    const dot = document.createElement("i"); dot.className = "run-dot";
    const status = document.createElement("span"); status.textContent = run.status.replaceAll("_", " ").toUpperCase();
    line.append(dot, status);
    const task = document.createElement("span"); task.className = "run-task"; task.textContent = run.task;
    const when = document.createElement("span"); when.className = "run-date"; when.textContent = dateLabel(run.created_at);
    button.append(line, task, when);
    button.addEventListener("click", () => selectTask(run.id));
    list.append(button);
  }
}
async function selectTask(id) {
  state.selectedId = id;
  renderRuns();
  await loadTask(id);
}
function renderNoTask() {
  $("#task-empty").hidden = false;
  $("#task-detail").hidden = true;
  $("#task-status").textContent = "IDLE";
  $("#task-status").className = "status-badge idle";
  text($("#task-heading"), "No task selected");
  text($("#attached-run"), "(none)");
  $("#approval-row").hidden = true;
  $("#approved-row").hidden = true;
}
async function loadTask(id) {
  try {
    const run = await request(`/v1/tasks/${encodeURIComponent(id)}`);
    renderTask(run);
    setApiState(true);
  } catch (error) {
    showNotice(`Could not load selected run: ${error.message}`);
  }
}
function renderTask(run) {
  $("#task-empty").hidden = true;
  $("#task-detail").hidden = false;
  text($("#task-heading"), run.task);
  const badge = $("#task-status");
  text(badge, run.status.replaceAll("_", " ").toUpperCase());
  badge.className = `status-badge ${run.status}`;
  text($("#run-id"), `RUN ${run.id.slice(0, 12).toUpperCase()}`);
  text($("#task-model"), run.model || (run.status === "planning" ? "planning" : "no model"));
  text($("#task-time"), dateLabel(run.created_at));
  text($("#task-request"), run.task);
  text($("#plan-model"), run.model || "");
  text($("#plan-content"), run.plan || run.error || (run.status === "planning" ? "Preparing your plan…" : "No plan was returned."));
  $("#approval-row").hidden = run.status !== "pending_approval";
  $("#approved-row").hidden = run.status !== "approved";
  text($("#attached-run"), `(${run.id.slice(0, 8)})`);
  const events = run.tool_events || [];
  text($("#activity-count"), `${events.length} ${events.length === 1 ? "event" : "events"}`);
  const list = $("#activity-list"); list.replaceChildren();
  if (!events.length) {
    const empty = document.createElement("div"); empty.className = "activity-empty"; empty.textContent = "No tools have run for this task."; list.append(empty); return;
  }
  for (const event of events) {
    const row = document.createElement("div"); row.className = "activity-item";
    const icon = document.createElement("span"); icon.className = "activity-icon"; icon.textContent = "›";
    const name = document.createElement("span"); name.textContent = event.tool_name.replaceAll("_", " ");
    const result = document.createElement("span"); result.className = `activity-result${event.succeeded ? "" : " fail"}`; result.textContent = event.succeeded ? "complete" : "failed";
    const time = document.createElement("span"); time.className = "activity-time"; time.textContent = `${event.duration_ms} ms`;
    row.append(icon, name, result, time); list.append(row);
  }
}
function setApiState(online) {
  const node = $("#api-state");
  text(node, online ? "API READY" : "API UNAVAILABLE");
  node.classList.toggle("error", !online);
}

$("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault(); clearNotice();
  const button = $("#plan-button"); button.disabled = true; button.textContent = "Generating plan…";
  try {
    const result = await request("/v1/plan", { method: "POST", body: JSON.stringify({ task: $("#task-input").value.trim(), workspace_root: $("#workspace-root").value.trim() || "." }) });
    await refreshRuns(false);
    await selectTask(result.run_id);
    $("#task-input").value = "";
    showNotice("Plan saved. Review it below and approve before preparing linked edits.", true);
  } catch (error) {
    showNotice(error.message);
  } finally { button.disabled = false; button.innerHTML = '<span class="button-glyph">✳</span> Generate plan <span class="button-arrow">↗</span>'; }
});

$("#approve-task").addEventListener("click", async () => {
  if (!state.selectedId) return;
  try { await request(`/v1/tasks/${encodeURIComponent(state.selectedId)}/approve`, { method: "POST" }); await refreshRuns(); }
  catch (error) { showNotice(error.message); }
});
$("#reject-task").addEventListener("click", async () => {
  if (!state.selectedId) return;
  try { await request(`/v1/tasks/${encodeURIComponent(state.selectedId)}/reject`, { method: "POST" }); await refreshRuns(); }
  catch (error) { showNotice(error.message); }
});
$("#new-task").addEventListener("click", () => { $("#task-input").focus(); window.scrollTo({ top: 0, behavior: "smooth" }); });
$("#refresh-button").addEventListener("click", () => refreshRuns());

$("#edit-form").addEventListener("submit", async (event) => {
  event.preventDefault(); clearNotice("#edit-message");
  const button = $("#preview-button"); button.disabled = true;
  try {
    const body = { workspace_root: $("#workspace-root").value.trim() || ".", path: $("#edit-path").value.trim(), content: $("#edit-content").value };
    if ($("#attach-task").checked && state.selectedId) body.task_id = state.selectedId;
    const preview = await request("/v1/edits/preview", { method: "POST", body: JSON.stringify(body) });
    state.previewId = preview.preview_id; state.previewPath = preview.path;
    text($("#diff-file"), preview.path);
    text($("#diff-hash"), preview.expected_sha256 ? `source sha256 ${preview.expected_sha256}` : "new file · no existing source hash");
    const diff = $("#diff-content"); diff.replaceChildren();
    for (const line of (preview.diff || "(no changes)").split(/(?<=\n)/)) {
      const span = document.createElement("span");
      span.className = line.startsWith("+") && !line.startsWith("+++") ? "added" : line.startsWith("-") && !line.startsWith("---") ? "removed" : "";
      span.textContent = line; diff.append(span);
    }
    $("#diff-panel").hidden = false;
    showNotice("Diff saved. Applying it will change the selected workspace file.", true, "#edit-message");
  } catch (error) { showNotice(error.message, false, "#edit-message"); }
  finally { button.disabled = false; }
});
$("#apply-edit").addEventListener("click", async () => {
  if (!state.previewId || !window.confirm(`Apply the reviewed diff to ${state.previewPath}? This changes the file on disk.`)) return;
  const button = $("#apply-edit"); button.disabled = true;
  try {
    const result = await request("/v1/edits/apply", { method: "POST", body: JSON.stringify({ preview_id: state.previewId, approved: true }) });
    state.previewId = null; $("#diff-panel").hidden = true;
    showNotice(`Applied ${result.path}.`, true, "#edit-message");
  } catch (error) { showNotice(error.message, false, "#edit-message"); }
  finally { button.disabled = false; }
});

async function initialize() {
  try { await request("/health"); setApiState(true); } catch { setApiState(false); }
  await refreshRuns();
}
initialize();
