"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  user: null,
  sources: [],
  view: "overview",
  searchRequest: 0,
  toastTimer: 0,
  demoMode: false,
  demoCredentials: null,
};

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const request = { ...options, headers, credentials: "same-origin" };
  const response = await fetch(path, request);
  if (response.status === 204) return null;

  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = body && typeof body === "object" ? body.error : null;
    throw new ApiError(error?.message || "The request could not be completed. Please try again.",
      response.status, error?.code);
  }
  return body;
}

function makeElement(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function kindLabel(kind) {
  return ({
    github: "GitHub",
    notes: "Notes",
    tasks: "Tasks",
    chat: "Chat",
    leaver_qa: "Leaver Q&A",
  })[kind] || "Source";
}

function kindInitial(kind) {
  return ({ github: "GH", notes: "N", tasks: "T", chat: "C", leaver_qa: "Q&A" })[kind] || "S";
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Recently added";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

function setServiceStatus(online, label) {
  for (const id of ["api-status-dot", "auth-status-dot"]) {
    const dot = document.getElementById(id);
    if (!dot) continue;
    dot.classList.toggle("is-online", online);
    dot.classList.toggle("is-offline", !online);
  }
  for (const id of ["api-status", "auth-api-status"]) {
    const text = document.getElementById(id);
    if (text) text.textContent = label;
  }
}

async function checkHealth() {
  try {
    const health = await api("/health");
    const online = health.status === "ok" && health.database === "ok";
    setServiceStatus(online, online ? "All systems ready" : "Service needs attention");
    state.demoMode = health.demo_mode === true;
    $("#demo-pill").hidden = !state.demoMode;
    return online;
  } catch {
    state.demoMode = false;
    setServiceStatus(false, "Connection unavailable");
    $("#demo-pill").hidden = true;
    return false;
  }
}

async function loadDemoCredentials() {
  const panel = $("#demo-access");
  const loginButton = $("#demo-login-button");
  panel.hidden = !state.demoMode;
  loginButton.hidden = !state.demoMode;
  loginButton.disabled = true;
  state.demoCredentials = null;
  if (!state.demoMode) return;

  try {
    state.demoCredentials = await api("/api/auth/demo-credentials");
    $("#demo-email").textContent = state.demoCredentials.email;
    $("#demo-access-message").hidden = true;
    loginButton.disabled = false;
  } catch (error) {
    $("#demo-email").textContent = "Unavailable";
    $("#demo-access-message").textContent = error.message;
    $("#demo-access-message").hidden = false;
  }
}

function showAuth(message = "") {
  state.user = null;
  $("#app-shell").hidden = true;
  $("#auth-screen").hidden = false;
  $("#auth-message").textContent = message;
  $("#auth-message").classList.remove("is-success");
}

function showWorkspace(user) {
  state.user = user;
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  const name = user.name || user.email;
  const firstName = name.split(/\s+/)[0];
  const initials = name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase();
  $("#user-name").textContent = name;
  $("#user-role").textContent = user.role;
  $("#user-avatar").textContent = initials;
  $("#topbar-avatar").textContent = initials;
  $("#welcome-name").textContent = firstName;
  $("#stat-clearance").textContent = ["Public", "Internal", "Confidential", "Restricted"][user.clearance] || "Custom";
  const canUpload = user.permissions.includes("upload_sources");
  $("#upload-panel").hidden = !canUpload;
  $("#upload-permission-note").hidden = canUpload;
  applyNavPermissions();
  const storedTheme = localStorage.getItem("baton-theme");
  if (storedTheme) { state.theme = storedTheme; document.documentElement.dataset.theme = storedTheme; }
}

function showToast(message, type = "success") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast is-${type}`;
  toast.hidden = false;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => { toast.hidden = true; }, 4200);
}

function renderEmpty(container, title, description, glyph = "✦") {
  container.replaceChildren();
  const empty = makeElement("div", "empty-state");
  empty.append(makeElement("span", "empty-icon", glyph), makeElement("strong", "", title),
    makeElement("p", "", description));
  container.append(empty);
}

function sourceCard(source, compact = false) {
  const button = makeElement("button", `source-card${compact ? " source-card-compact" : ""}`);
  button.type = "button";
  button.dataset.sourceId = String(source.id);
  button.setAttribute("aria-label", `Open ${source.name}`);

  const icon = makeElement("span", `source-file-icon kind-${source.kind}`, kindInitial(source.kind));
  icon.setAttribute("aria-hidden", "true");
  const main = makeElement("span", "source-main");
  main.append(makeElement("span", "source-name", source.name));
  const subline = makeElement("span", "source-subline");
  subline.append(makeElement("span", "source-kind-label", kindLabel(source.kind)),
    makeElement("span", "", "·"),
    makeElement("span", "", `${source.chunk_count} ${source.chunk_count === 1 ? "chunk" : "chunks"}`));
  main.append(subline);

  const side = makeElement("span", "source-side");
  const clearance = makeElement("span", `clearance-tag clearance-${source.clearance}`, source.clearance_label);
  side.append(clearance);
  if (!compact) side.append(makeElement("span", "source-date", formatDate(source.created_at)));
  const status = makeElement("span", `source-status${source.status === "failed" ? " status-failed" : ""}`,
    source.status === "failed" ? "Needs attention" : "Ready");
  side.append(status);
  button.append(icon, main, side);
  return button;
}

function visibleSources() {
  const name = ($("#source-filter").value || "").trim().toLowerCase();
  const kind = $("#kind-filter").value;
  const clearance = $("#clearance-filter").value;
  return state.sources.filter(source =>
    (!name || source.name.toLowerCase().includes(name)) &&
    (!kind || source.kind === kind) &&
    (clearance === "" || source.clearance === Number(clearance)));
}

function renderSources() {
  const all = $("#all-sources");
  const overview = $("#overview-sources");
  const filtered = visibleSources();
  all.replaceChildren();
  overview.replaceChildren();
  $("#source-total").textContent = String(state.sources.length);
  $("#nav-source-count").textContent = String(state.sources.length);
  $("#list-footer").textContent = filtered.length === state.sources.length
    ? `${filtered.length} ${filtered.length === 1 ? "source" : "sources"} in your view`
    : `Showing ${filtered.length} of ${state.sources.length} sources`;

  if (filtered.length) {
    for (const source of filtered) all.append(sourceCard(source));
  } else {
    renderEmpty(all, state.sources.length ? "No sources match those filters" : "Your knowledge base starts here",
      state.sources.length ? "Try changing the type, visibility, or search filters." :
        "When sources are added, their verified context will appear here.", "⌕");
  }

  if (state.sources.length) {
    for (const source of state.sources.slice(0, 4)) overview.append(sourceCard(source, true));
  } else {
    renderEmpty(overview, "Nothing collected yet", "Add a source to give your team's knowledge a place to live.", "＋");
  }

  const chunks = state.sources.reduce((total, source) => total + source.chunk_count, 0);
  const redactions = state.sources.reduce((total, source) =>
    total + Object.values(source.redactions || {}).reduce((sum, count) => sum + Number(count || 0), 0), 0);
  $("#stat-sources").textContent = String(state.sources.length);
  $("#stat-chunks").textContent = String(chunks);
  $("#stat-redactions").textContent = String(redactions);
}

async function refreshWorkspace() {
  const sources = await api("/api/sources");
  state.sources = sources;
  renderSources();
}

function setView(view) {
  state.view = view;
  for (const section of $$(".page-view")) section.hidden = section.id !== `${view}-view`;
  for (const button of $$("[data-view]")) {
    if (button.classList.contains("nav-link")) button.classList.toggle("is-active", button.dataset.view === view);
  }
  $("#breadcrumb-current").textContent = view === "overview" ? "Overview" : "Sources";
  $("#sidebar").classList.remove("is-open");
  if (view === "sources") $("#source-filter").focus({ preventScroll: true });
}

function appendMeta(label, value, className = "") {
  const chip = makeElement("span", `meta-chip${className ? ` ${className}` : ""}`);
  chip.append(makeElement("span", "", `${label}:`), makeElement("strong", "", value));
  $("#detail-meta").append(chip);
}

async function openSource(sourceId) {
  const dialog = $("#source-dialog");
  $("#detail-title").textContent = "Loading source…";
  $("#detail-meta").replaceChildren();
  $("#detail-content").replaceChildren();
  $("#detail-content").append(makeElement("div", "detail-loading", "Loading source details…"));
  if (!dialog.open) dialog.showModal();

  try {
    const [source, chunks] = await Promise.all([
      api(`/api/sources/${encodeURIComponent(sourceId)}`),
      api(`/api/sources/${encodeURIComponent(sourceId)}/chunks`),
    ]);
    $("#detail-title").textContent = source.name;
    $("#detail-icon").textContent = kindInitial(source.kind);
    $("#detail-icon").className = `detail-file-icon kind-${source.kind}`;
    $("#detail-meta").replaceChildren();
    appendMeta("Type", kindLabel(source.kind));
    appendMeta("Visibility", source.clearance_label, `clearance-${source.clearance}`);
    appendMeta("Size", formatBytes(source.byte_size));
    appendMeta("Added", formatDate(source.created_at));
    appendMeta("Evidence", `${source.chunk_count} ${source.chunk_count === 1 ? "chunk" : "chunks"}`);
    const content = $("#detail-content");
    content.replaceChildren();
    if (source.status === "failed") {
      content.append(makeElement("p", "detail-error", source.error || "This source could not be processed."));
      return;
    }
    if (!chunks.length) {
      content.append(makeElement("div", "search-empty", "No text chunks are available for this source."));
      return;
    }
    for (const chunk of chunks) {
      const card = makeElement("article", "chunk-card");
      const heading = makeElement("div", "chunk-heading");
      heading.append(makeElement("strong", "", chunk.locator || `Evidence ${chunk.ordinal + 1}`));
      if (chunk.author) heading.append(makeElement("span", "", `· ${chunk.author}`));
      if (chunk.occurred_at) heading.append(makeElement("span", "", `· ${formatDate(chunk.occurred_at)}`));
      card.append(heading, makeElement("p", "chunk-text", chunk.text));
      content.append(card);
    }
  } catch (error) {
    $("#detail-title").textContent = "Source unavailable";
    $("#detail-content").replaceChildren(makeElement("p", "detail-error",
      error instanceof ApiError && error.status === 404
        ? "This source isn't available at your current access level."
        : error.message));
  }
}

function renderSearchEmpty(message, initial = false) {
  const results = $("#search-results");
  results.replaceChildren();
  const empty = makeElement("div", `search-empty${initial ? " search-empty-initial" : ""}`);
  empty.append(makeElement("span", "search-empty-icon", initial ? "⌕" : "✳"),
    makeElement("span", "", message));
  results.append(empty);
}

function renderSearchResults(query, hits) {
  const results = $("#search-results");
  results.replaceChildren();
  if (!hits.length) {
    renderSearchEmpty(`No evidence found for “${query}”.`);
    return;
  }
  for (const hit of hits.slice(0, 4)) {
    const button = makeElement("button", "search-result");
    button.type = "button";
    button.dataset.sourceId = String(hit.source_id);
    const title = makeElement("span", "search-result-title");
    title.append(makeElement("span", "", hit.source_name),
      makeElement("span", "", hit.locator || `Source ${hit.source_id}`));
    button.append(title, makeElement("span", "search-result-snippet", hit.snippet));
    results.append(button);
  }
}

async function searchKnowledge() {
  const query = $("#search-query").value.trim();
  const requestId = ++state.searchRequest;
  if (query.length < 2) {
    renderSearchEmpty("Your next answer might already be here.", true);
    return;
  }
  const results = $("#search-results");
  results.replaceChildren();
  const loading = makeElement("div", "detail-loading");
  loading.append(makeElement("span", "spinner"), makeElement("span", "", "Searching your sources…"));
  results.append(loading);
  try {
    const hits = await api(`/api/search?q=${encodeURIComponent(query)}&limit=20`);
    if (requestId === state.searchRequest) renderSearchResults(query, hits);
  } catch (error) {
    if (requestId !== state.searchRequest) return;
    renderSearchEmpty(error.message);
  }
}

async function uploadSource(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const file = $("#source-file").files[0];
  if (!file) {
    $("#upload-message").textContent = "Choose a file before adding a source.";
    return;
  }
  if (file.size > 1_000_000) {
    $("#upload-message").textContent = "That file is over the 1 MB limit. Choose a smaller file.";
    return;
  }

  const button = $("#upload-button");
  button.disabled = true;
  button.textContent = "Adding source…";
  $("#upload-message").textContent = "";
  const data = new FormData();
  data.append("file", file);
  data.append("clearance", $("#clearance-select").value);
  if ($("#kind-select").value) data.append("kind", $("#kind-select").value);

  try {
    const result = await api("/api/sources", {
      method: "POST",
      headers: { "X-Baton-Client": "web" },
      body: data,
    });
    form.reset();
    $("#file-name").textContent = "Choose a file";
    $("#file-hint").textContent = "or drop it here";
    $("#upload-message").textContent = result.message;
    $("#upload-message").classList.toggle("is-success", true);
    showToast(result.message, "success");
    await refreshWorkspace();
    if (result.source) await openSource(result.source.id);
  } catch (error) {
    $("#upload-message").textContent = error.message;
    $("#upload-message").classList.remove("is-success");
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.innerHTML = 'Add source <span aria-hidden="true">↗</span>';
  }
}

async function restoreSession() {
  await checkHealth();
  await loadDemoCredentials();
  try {
    const user = await api("/api/auth/me");
    showWorkspace(user);
    await refreshWorkspace();
    if (state.user) refreshOverviewAnalytics();
  } catch (error) {
    showAuth(error instanceof ApiError && error.status !== 401
      ? "The workspace is temporarily unavailable. Please try again."
      : "");
  }
}

$("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#login-button");
  const label = $("span", button);
  const message = $("#auth-message");
  const previousLabel = label.textContent;
  button.disabled = true;
  $("#demo-login-button").disabled = true;
  label.textContent = "Signing in…";
  message.textContent = "";

  try {
    const user = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("#email").value.trim(), password: $("#password").value }),
    });
    message.textContent = "Signed in. Loading your workspace…";
    message.classList.add("is-success");
    showWorkspace(user.user);
    await refreshWorkspace();
  } catch (error) {
    message.textContent = error.message;
    message.classList.remove("is-success");
  } finally {
    button.disabled = false;
    $("#demo-login-button").disabled = !state.demoCredentials;
    label.textContent = previousLabel;
  }
});

$("#password-toggle").addEventListener("click", event => {
  const input = $("#password");
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  event.currentTarget.textContent = showing ? "Show" : "Hide";
  event.currentTarget.setAttribute("aria-label", showing ? "Show password" : "Hide password");
});

$("#demo-login-button").addEventListener("click", () => {
  if (!state.demoCredentials) return;
  $("#email").value = state.demoCredentials.email;
  $("#password").value = state.demoCredentials.password;
  $("#login-form").requestSubmit();
});

$("#logout-button").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST", headers: { "X-Baton-Client": "web" } });
  } catch {
    showToast("You were signed out locally. The server session may expire later.", "error");
  }
  showAuth();
  $("#login-form").reset();
});

for (const button of $$("[data-view]")) {
  button.addEventListener("click", () => setView(button.dataset.view));
}

$("#mobile-menu-button").addEventListener("click", () => {
  $("#sidebar").classList.toggle("is-open");
});

document.addEventListener("click", event => {
  const source = event.target.closest("[data-source-id]");
  if (source) openSource(source.dataset.sourceId);
  if (event.target === $("#source-dialog")) $("#source-dialog").close();
});

$("#detail-close").addEventListener("click", () => $("#source-dialog").close());
$("#source-dialog").addEventListener("click", event => {
  if (event.target === $("#source-dialog")) $("#source-dialog").close();
});

$("#source-filter").addEventListener("input", renderSources);
$("#kind-filter").addEventListener("change", renderSources);
$("#clearance-filter").addEventListener("change", renderSources);
$("#upload-form").addEventListener("submit", uploadSource);
$("#source-file").addEventListener("change", event => {
  const file = event.currentTarget.files[0];
  $("#file-name").textContent = file ? file.name : "Choose a file";
  $("#file-hint").textContent = file ? formatBytes(file.size) : "or drop it here";
  $("#upload-message").textContent = "";
  $("#upload-message").classList.remove("is-success");
});

$("#search-form").addEventListener("submit", event => {
  event.preventDefault();
  searchKnowledge();
});

$("#search-query").addEventListener("input", () => {
  window.clearTimeout(state.searchTimer);
  state.searchTimer = window.setTimeout(searchKnowledge, 280);
});

restoreSession();

/* --- handover workspace views ------------------------------------------------------------------ */

const LABEL_META = {
  supported: { name: "Supported", cls: "label-supported" },
  single_source: { name: "Single source", cls: "label-single_source" },
  conflicting: { name: "Conflicting", cls: "label-conflicting" },
  stale: { name: "Stale", cls: "label-stale" },
  missing: { name: "Missing", cls: "label-missing" },
};
const REVIEW_META = {
  proposed: { name: "Proposed", cls: "review-proposed" },
  accepted: { name: "Accepted", cls: "review-accepted" },
  rejected: { name: "Rejected", cls: "review-rejected" },
};
const VIEW_TITLES = {
  overview: "Overview", sources: "Sources", extraction: "Extraction", claims: "Knowledge base",
  review: "Review", conflicts: "Conflicts", questions: "Questions", assistant: "Assistant",
  handover: "Handover", audit: "Audit",
};

Object.assign(state, {
  claims: [], conflicts: [], questions: [], jobs: [], audit: [], handover: null,
  labelFilter: "", claimFilter: "", thread: [], loaded: new Set(), theme: "light",
});

function labelChip(label) {
  const meta = LABEL_META[label] || { name: label, cls: "label-missing" };
  return `<span class="label-chip ${meta.cls}">${meta.name}</span>`;
}

function reviewChip(status) {
  const meta = REVIEW_META[status] || { name: status, cls: "review-proposed" };
  return `<span class="review-chip ${meta.cls}">${meta.name}</span>`;
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, ch =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function viewLoading(message = "Loading…") {
  return `<div class="view-loading"><span class="spinner"></span><span>${escapeHtml(message)}</span></div>`;
}

function viewError(message, retry) {
  return `<div class="view-error"><span>${escapeHtml(message)}</span>
    <button class="button button-small button-quiet" id="retry-btn" type="button">Try again</button></div>`;
}

async function loadInto(containerId, loader, renderer) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = viewLoading();
  try {
    const data = await loader();
    container.innerHTML = "";
    renderer(container, data);
  } catch (error) {
    container.innerHTML = viewError(error.message);
    const retryBtn = container.querySelector("#retry-btn");
    if (retryBtn) retryBtn.addEventListener("click", async () => {
      container.innerHTML = viewLoading();
      try {
        const data = await loader();
        container.innerHTML = "";
        renderer(container, data);
      } catch (retryError) {
        container.innerHTML = viewError(retryError.message);
      }
    });
  }
}

function applyNavPermissions() {
  const allowed = new Set(state.user?.permissions || []);
  for (const button of $$("[data-permission]")) {
    button.hidden = !allowed.has(button.dataset.permission);
  }
  for (const caption of $$(".nav-caption[data-nav-group]")) {
    const next = caption.nextElementSibling;
    caption.hidden = !(next instanceof HTMLElement) || ![...next.querySelectorAll(".nav-link")].some(link => !link.hidden);
  }
}

/* --- overview: analytics + activity ------------------------------------------------------------- */

function renderLabelBars(container, claims) {
  const counts = {};
  for (const claim of claims) counts[claim.label] = (counts[claim.label] || 0) + 1;
  const order = ["supported", "single_source", "conflicting", "stale", "missing"];
  const total = order.reduce((sum, label) => sum + (counts[label] || 0), 0) || 1;
  container.replaceChildren();
  if (!claims.length) {
    container.append(makeElement("p", "handover-note", "Run the pipeline to classify your first claims."));
    return;
  }
  for (const label of order) {
    const count = counts[label] || 0;
    const row = makeElement("div", "label-bar-row");
    row.append(makeElement("span", "", LABEL_META[label].name));
    const track = makeElement("span", "bar-track");
    const fill = makeElement("span", `bar-fill bar-${label}`);
    fill.style.width = `${Math.round((count / total) * 100)}%`;
    track.append(fill);
    row.append(track, makeElement("strong", "", String(count)));
    container.append(row);
  }
}

function renderActivity(container, events) {
  container.replaceChildren();
  if (!events.length) {
    container.append(makeElement("p", "handover-note", "Activity appears here as the team works."));
    return;
  }
  for (const event of events.slice(0, 8)) {
    const row = makeElement("div", "activity-row");
    row.append(makeElement("span", "activity-action", event.action),
      makeElement("span", "", event.actor_label), makeElement("time", "", formatDateTime(event.created_at)));
    container.append(row);
  }
}

async function refreshOverviewAnalytics() {
  try {
    const [handover, events] = await Promise.all([api("/api/handover"), api("/api/audit?limit=8")]);
    renderLabelBars($("#label-bars"), handover.claims || []);
    renderActivity($("#activity-feed"), events || []);
  } catch {
    $("#label-bars").replaceChildren(makeElement("p", "handover-note", "Run the pipeline to see knowledge health."));
    $("#activity-feed").replaceChildren(makeElement("p", "handover-note", "No events recorded yet."));
  }
}

/* --- extraction --------------------------------------------------------------------------------- */

async function runPipeline(force = false) {
  const button = $("#run-pipeline-button");
  const status = $("#pipeline-status");
  button.disabled = true;
  status.hidden = false;
  $("#pipeline-stage").textContent = "Extracting and verifying claims…";
  $("#pipeline-note").textContent = "Quotes are checked against the original chunks.";
  $("#pipeline-spinner").hidden = false;
  try {
    const result = await api("/api/pipeline/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }),
    });
    renderPipelineResult(result);
    showToast(`Pipeline complete: ${result.claims} claims, ${result.evidence} verified citations.`, "success");
  } catch (error) {
    if (error.status === 409) {
      $("#pipeline-spinner").hidden = true;
      $("#pipeline-stage").textContent = "The pipeline already ran on these sources.";
      $("#pipeline-note").textContent = "Use “Run again” to re-extract.";
      renderPipelineActions(true);
    } else {
      $("#pipeline-stage").textContent = "The pipeline could not run.";
      $("#pipeline-note").textContent = error.message;
      $("#pipeline-spinner").hidden = true;
    }
  } finally {
    button.disabled = false;
    loadJobs();
  }
}

function renderPipelineActions(force) {
  const note = $("#pipeline-note");
  note.replaceChildren();
  const again = makeElement("button", "button button-small button-quiet", force ? "Run again (force)" : "Dismiss");
  again.type = "button";
  again.addEventListener("click", async () => {
    if (force) { await runPipeline(true); return; }
    $("#pipeline-status").hidden = true;
  });
  note.append(again);
}

function renderPipelineResult(result) {
  $("#pipeline-spinner").hidden = true;
  $("#pipeline-stage").textContent = `Extracted ${result.claims} claims from ${result.evidence} verified citations.`;
  $("#pipeline-note").textContent = result.dropped
    ? `${result.dropped} unverifiable claims were dropped.` : "No claims were dropped.";
  const stats = $("#pipeline-stats");
  stats.hidden = false;
  stats.replaceChildren();
  const cards = [
    ["Claims", result.claims, "stat-icon-lilac"], ["Verified citations", result.evidence, "stat-icon-mint"],
    ["Conflicts", result.conflicts, "stat-icon-peach"], ["Questions", result.questions, "stat-icon-blue"],
  ];
  for (const [label, value, icon] of cards) {
    const card = makeElement("article", "stat-card");
    const top = makeElement("div", "stat-top");
    const iconSpan = makeElement("span", `stat-icon ${icon}`, "✦");
    iconSpan.setAttribute("aria-hidden", "true");
    top.append(iconSpan, makeElement("span", "stat-trend", "PIPELINE"));
    card.append(top, makeElement("strong", "stat-value", String(value)), makeElement("span", "stat-label", label));
    stats.append(card);
  }
}

function renderJobs(container, jobs) {
  container.replaceChildren();
  if (!jobs.length) {
    container.append(makeElement("div", "search-empty", "No jobs yet. Run the pipeline to extract your first claims."));
    return;
  }
  for (const job of jobs.slice(0, 10)) {
    const card = makeElement("div", "job-card");
    const icon = makeElement("span", "stat-icon stat-icon-lilac", "✦");
    icon.setAttribute("aria-hidden", "true");
    const main = makeElement("span");
    main.append(makeElement("strong", "", `${job.kind} · ${job.id.slice(0, 8)}…`));
    const stage = job.stage || job.status;
    main.append(makeElement("small", "", `${stage} · started ${formatDateTime(job.created_at)}${job.error ? ` · ${job.error}` : ""}`));
    const statusClass = job.status === "succeeded" ? "is-done" : job.status === "failed" ? "is-failed"
      : job.status === "running" ? "is-running" : "";
    card.append(icon, main, makeElement("span", `job-status ${statusClass}`, job.status));
    container.append(card);
  }
}

async function loadJobs() {
  await loadInto("#jobs-list", () => api("/api/pipeline/jobs"), renderJobs);
}

/* --- knowledge base ----------------------------------------------------------------------------- */

async function loadClaims() {
  state.claims = await api("/api/review/claims");
  renderLabelStrip();
  renderClaims();
}

function renderLabelStrip() {
  const strip = $("#label-strip");
  strip.replaceChildren();
  const counts = {};
  for (const claim of state.claims) counts[claim.label] = (counts[claim.label] || 0) + 1;
  const all = makeElement("button", state.labelFilter === "" ? "is-on" : "", `All · ${state.claims.length}`);
  all.type = "button";
  all.addEventListener("click", () => { state.labelFilter = ""; renderLabelStrip(); renderClaims(); });
  strip.append(all);
  for (const [label, meta] of Object.entries(LABEL_META)) {
    if (!counts[label]) continue;
    const button = makeElement("button", state.labelFilter === label ? "is-on" : "", "");
    button.append(makeElement("span", "", meta.name), makeElement("small", "", String(counts[label])));
    button.type = "button";
    button.addEventListener("click", () => { state.labelFilter = state.labelFilter === label ? "" : label; renderLabelStrip(); renderClaims(); });
    strip.append(button);
  }
}

function renderClaims() {
  const body = $("#claims-tbody");
  body.replaceChildren();
  const needle = ($("#claim-filter").value || "").trim().toLowerCase();
  const rows = state.claims.filter(claim =>
    (!state.labelFilter || claim.label === state.labelFilter) &&
    (!needle || `${claim.subject} ${claim.attribute} ${claim.value || ""} ${claim.statement}`.toLowerCase().includes(needle)));
  $("#claims-total").textContent = String(rows.length);
  $("#nav-claim-count").textContent = String(state.claims.length);
  if (!rows.length) {
    body.append(Object.assign(document.createElement("tr"), { innerHTML:
      `<td colspan="5"><div class="search-empty">No claims match. Run the pipeline from Extraction to propose claims.</div></td>` }));
    return;
  }
  for (const claim of rows) {
    const row = document.createElement("tr");
    row.style.cursor = "pointer";
    const main = makeElement("td", "claim-cell");
    main.append(makeElement("strong", "", claim.statement),
      makeElement("small", "", `${claim.subject} · ${claim.attribute} · v${claim.version}`));
    const value = makeElement("td", "claim-value", claim.value ?? "—");
    const labelCell = makeElement("td");
    labelCell.innerHTML = labelChip(claim.label);
    const reviewCell = makeElement("td");
    reviewCell.innerHTML = reviewChip(claim.review_status);
    const evidence = makeElement("td", "claim-evidence", `${claim.evidence.length} citation${claim.evidence.length === 1 ? "" : "s"}`);
    row.replaceChildren(main, value, labelCell, reviewCell, evidence);
    row.addEventListener("click", () => openClaim(claim));
    body.append(row);
  }
}

function openClaim(claim) {
  const dialog = $("#claim-dialog");
  $("#claim-dialog-title").textContent = `${claim.subject} · ${claim.attribute}`;
  const icon = $("#claim-dialog-icon");
  icon.textContent = "C";
  icon.className = "detail-file-icon kind-notes";
  const meta = $("#claim-dialog-meta");
  meta.replaceChildren();
  for (const [label, value] of [
    ["Statement", claim.statement], ["Value", claim.value ?? "—"], ["Label", (LABEL_META[claim.label] || {}).name || claim.label],
    ["Review", (REVIEW_META[claim.review_status] || {}).name || claim.review_status], ["Version", `v${claim.version}`],
    ["Clearance", String(claim.required_clearance)],
  ]) {
    const chip = makeElement("span", "meta-chip");
    chip.append(makeElement("span", "", `${label}:`), makeElement("strong", "", value));
    meta.append(chip);
  }
  const content = $("#claim-dialog-content");
  content.replaceChildren();
  const statement = makeElement("p", "chunk-text", claim.statement);
  content.append(statement);
  const heading = makeElement("h3", "", "Evidence");
  heading.style.cssText = "margin: 14px 0 8px; color: #33405a; font-size: 11px;";
  content.append(heading);
  if (!claim.evidence.length) {
    content.append(makeElement("p", "handover-note", "No citations were stored for this claim."));
  }
  for (const ev of claim.evidence) {
    const card = makeElement("div", `chunk-card${ev.verified ? "" : " quote-not-verified"}`);
    const head = makeElement("div", "chunk-heading");
    head.append(makeElement("strong", "", ev.source_name), makeElement("span", "", ev.locator ? ` · ${ev.locator}` : ""));
    card.append(head, makeElement("p", "chunk-text", `“${ev.quote}”`));
    content.append(card);
  }
  if (!dialog.open) dialog.showModal();
}

/* --- review ------------------------------------------------------------------------------------- */

function renderReviewQueue(container) {
  container.replaceChildren();
  const queue = state.claims.filter(claim => claim.review_status === "proposed");
  $("#review-queue-count").textContent = String(queue.length);
  if (!queue.length) {
    renderEmpty(container, "The queue is clear", "Every proposed claim has a decision. Run the pipeline again after new sources arrive.", "✓");
    return;
  }
  for (const claim of queue) container.append(reviewCard(claim));
}

function reviewCard(claim) {
  const card = makeElement("article", "queue-card");
  const head = makeElement("div", "queue-head");
  head.append(makeElement("h3", "", `${claim.subject} · ${claim.attribute}`));
  head.insertAdjacentHTML("beforeend", labelChip(claim.label));
  head.insertAdjacentHTML("beforeend", reviewChip(claim.review_status));
  const meta = makeElement("div", "queue-meta");
  meta.append(makeElement("span", "", `Version ${claim.version}`),
    makeElement("span", "", `Updated ${formatDateTime(claim.updated_at || claim.created_at)}`),
    claim.conflict_id ? makeElement("span", "", `Conflict #${claim.conflict_id}`) : makeElement("span", "", "No conflict"));
  const statement = makeElement("p", "queue-statement", claim.statement);
  card.append(head, meta, statement);

  const evidence = makeElement("ul", "evidence-list");
  for (const ev of claim.evidence) {
    const item = document.createElement("li");
    if (!ev.verified) item.classList.add("quote-not-verified");
    item.append(makeElement("span", "quote", `“${ev.quote}”`),
      makeElement("span", "where", `${ev.source_name}${ev.locator ? ` · ${ev.locator}` : ""}${ev.verified ? "" : " · unverified"}`));
    evidence.append(item);
  }
  card.append(evidence);

  const form = makeElement("div", "review-form");
  const statementLabel = makeElement("label", "", "Statement");
  const statementArea = document.createElement("textarea");
  statementArea.value = claim.statement;
  statementLabel.append(statementArea);
  const valueLabel = makeElement("label", "", "Value");
  const valueInput = makeElement("input");
  valueInput.value = claim.value ?? "";
  valueInput.placeholder = "The value this claim asserts";
  valueLabel.append(valueInput);
  const noteLabel = makeElement("label", "", "Decision note");
  const noteInput = makeElement("input");
  noteInput.placeholder = "Why this decision (optional)";
  noteLabel.append(noteInput);
  const bar = makeElement("div", "decision-bar");
  const accept = makeElement("button", "button button-small button-primary", "Accept");
  const reject = makeElement("button", "button button-small button-danger", "Reject");
  const save = makeElement("button", "button button-small button-quiet", "Save edit");
  accept.type = reject.type = save.type = "button";
  const decide = async (action) => {
    accept.disabled = reject.disabled = save.disabled = true;
    try {
      const body = { statement: statementArea.value.trim() || undefined, value: valueInput.value.trim() || undefined, note: noteInput.value.trim() || undefined };
      const result = await api(`/api/review/claims/${claim.id}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Baton-Client": "web" }, body: JSON.stringify(body),
      });
      claim.review_status = result.claim.review_status;
      claim.version = result.version;
      showToast(`Claim ${action === "accept" ? "accepted" : "rejected"} at version ${result.version}.`, "success");
      card.classList.add("is-decided");
      card.querySelector(".queue-head").insertAdjacentHTML("beforeend", reviewChip(claim.review_status));
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      accept.disabled = reject.disabled = save.disabled = false;
      loadClaims().then(renderReviewQueueIfReview);
    }
  };
  accept.addEventListener("click", () => decide("accept"));
  reject.addEventListener("click", () => decide("reject"));
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const body = { statement: statementArea.value.trim() || undefined, value: valueInput.value.trim() || undefined, note: noteInput.value.trim() || undefined };
      const result = await api(`/api/review/claims/${claim.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json", "X-Baton-Client": "web" }, body: JSON.stringify(body),
      });
      showToast(`Claim edited to version ${result.version}.`, "success");
      claim.statement = result.claim.statement;
      claim.value = result.claim.value;
      claim.version = result.version;
      loadClaims().then(renderReviewQueueIfReview);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      save.disabled = false;
    }
  });
  bar.append(accept, reject, save);
  const note = makeElement("p", "queue-note", "Editing keeps the claim proposed; accept or reject to decide it.");
  form.append(statementLabel, valueLabel, noteLabel, bar, note);
  card.append(form);
  return card;
}

function renderReviewQueueIfReview() {
  if (state.view === "review") renderReviewQueue($("#review-queue"));
}

/* --- conflicts + questions ---------------------------------------------------------------------- */

function renderConflicts(container, conflicts) {
  container.replaceChildren();
  $("#conflicts-total").textContent = String(conflicts.length);
  if (!conflicts.length) {
    renderEmpty(container, "No conflicts", "Sources agree everywhere the pipeline looked.", "✓");
    return;
  }
  for (const conflict of conflicts) {
    const card = makeElement("article", `conflict-card${conflict.status === "resolved" ? " is-resolved" : ""}`);
    const head = makeElement("div", "conflict-head");
    head.append(makeElement("h3", "", `${conflict.subject} · ${conflict.attribute}`));
    head.insertAdjacentHTML("beforeend", `<span class="review-chip ${conflict.status === "open" ? "review-proposed" : "review-accepted"}">${escapeHtml(conflict.status)}</span>`);
    card.append(head);
    const values = makeElement("div", "conflict-values");
    for (const claim of conflict.claims) {
      const box = makeElement("div", "conflict-value");
      box.append(makeElement("strong", "", `${claim.label} · v${claim.version}`),
        makeElement("p", "", claim.value ?? claim.statement));
      values.append(box);
    }
    card.append(values);
    container.append(card);
  }
}

function renderQuestions(container, questions) {
  container.replaceChildren();
  const open = questions.filter(q => q.status === "open");
  $("#questions-total").textContent = String(open.length);
  if (!questions.length) {
    renderEmpty(container, "No open questions", "The pipeline found no gaps worth asking about.", "✓");
    return;
  }
  const canAnswer = (state.user?.permissions || []).includes("answer_questions");
  for (const question of questions) {
    const card = makeElement("article", "question-card");
    const head = makeElement("div", "queue-head");
    head.append(makeElement("h3", "", question.text));
    head.insertAdjacentHTML("beforeend", `<span class="review-chip ${question.status === "open" ? "review-proposed" : "review-accepted"}">${escapeHtml(question.status)}</span>`);
    head.insertAdjacentHTML("beforeend", makeElement("span", "meta-chip", `kind: ${question.kind}`));
    card.append(head);
    if (question.status === "answered") {
      const box = makeElement("div", "question-answer");
      box.append(makeElement("strong", "", `Answered ${formatDateTime(question.answered_at)}`),
        makeElement("p", "", question.answer || ""));
      card.append(box);
    } else if (canAnswer) {
      const form = makeElement("form", "answer-form");
      const label = makeElement("label", "", "Your answer");
      const area = document.createElement("textarea");
      area.required = true;
      area.placeholder = "Answer with the source you relied on…";
      label.append(area);
      const submit = makeElement("button", "button button-small button-primary", "Record answer");
      submit.type = "submit";
      form.append(label, submit);
      form.addEventListener("submit", async event => {
        event.preventDefault();
        submit.disabled = true;
        try {
          await api(`/api/questions/${question.id}/answer`, {
            method: "POST", headers: { "Content-Type": "application/json", "X-Baton-Client": "web" },
            body: JSON.stringify({ answer: area.value.trim() }),
          });
          showToast("Answer recorded.", "success");
          loadQuestions();
        } catch (error) {
          showToast(error.message, "error");
          submit.disabled = false;
        }
      });
      card.append(form);
    }
    container.append(card);
  }
}

async function loadQuestions() {
  await loadInto("#question-list", () => api("/api/questions"), renderQuestions);
}

/* --- assistant ---------------------------------------------------------------------------------- */

function renderChat() {
  const log = $("#chat-log");
  log.replaceChildren();
  if (!state.thread.length) {
    log.append($("#chat-empty").cloneNode(true));
    return;
  }
  for (const turn of state.thread) {
    const row = makeElement("div", `chat-msg${turn.role === "user" ? " is-user" : ""}${turn.refused ? " chat-refused" : ""}`);
    row.append(makeElement("div", "chat-bubble", turn.text));
    if (turn.citations?.length) {
      const cites = makeElement("div", "chat-citations");
      for (const cite of turn.citations) {
        const chip = makeElement("span", "citation");
        chip.innerHTML = `<q>${escapeHtml(cite.quote)}</q><span class="cite-source">${escapeHtml(cite.source_name)}${cite.locator ? ` · ${escapeHtml(cite.locator)}` : ""}</span>`;
        cites.append(chip);
      }
      row.append(cites);
    }
    log.append(row);
  }
  log.scrollTop = log.scrollHeight;
}

async function askAssistant(event) {
  event.preventDefault();
  const input = $("#chat-input");
  const question = input.value.trim();
  if (question.length < 2) return;
  input.value = "";
  state.thread.push({ role: "user", text: question });
  renderChat();
  const button = $("#chat-send-button");
  button.disabled = true;
  state.thread.push({ role: "assistant", text: "Checking the evidence…" });
  renderChat();
  try {
    const answer = await api("/api/assistant/ask", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }),
    });
    state.thread.at(-1) = { role: "assistant", text: answer.answer, citations: answer.citations, refused: answer.refused };
  } catch (error) {
    state.thread.at(-1) = { role: "assistant", text: error.message, refused: true };
  } finally {
    button.disabled = false;
    renderChat();
    input.focus();
  }
}

/* --- handover ----------------------------------------------------------------------------------- */

async function loadHandover() {
  const view = await api("/api/handover");
  state.handover = view;
  const stats = $("#handover-stats");
  stats.replaceChildren();
  const cards = [
    ["Claims", view.stats.claims, "stat-icon-lilac"], ["Accepted", view.stats.accepted, "stat-icon-mint"],
    ["Conflicts open", view.stats.conflicts_open, "stat-icon-peach"], ["Questions open", view.stats.questions_open, "stat-icon-blue"],
  ];
  for (const [label, value, icon] of cards) {
    const card = makeElement("article", "stat-card");
    const top = makeElement("div", "stat-top");
    const iconSpan = makeElement("span", `stat-icon ${icon}`, "✦");
    iconSpan.setAttribute("aria-hidden", "true");
    top.append(iconSpan, makeElement("span", "stat-trend", "HANDOVER"));
    card.append(top, makeElement("strong", "stat-value", String(value)), makeElement("span", "stat-label", label));
    stats.append(card);
  }
  const summary = $("#handover-summary");
  summary.replaceChildren();
  const rows = [
    ["Sources", view.stats.sources], ["Evidence chunks", view.stats.chunks], ["Claims", view.stats.claims],
    ["Accepted claims", view.stats.accepted], ["Conflicts (open / resolved)", `${view.stats.conflicts_open} / ${view.stats.conflicts_resolved}`],
    ["Questions (open / answered)", `${view.stats.questions_open} / ${view.stats.questions_answered}`],
    ["Protected details", view.stats.redactions],
  ];
  for (const [label, value] of rows) {
    const line = makeElement("div", "handover-row");
    line.append(makeElement("span", "", label), makeElement("strong", "", String(value)));
    summary.append(line);
  }
  const facts = $("#ack-facts");
  facts.replaceChildren();
  const withheldText = Object.entries(view.withheld || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "none";
  for (const [label, value] of [
    ["Viewer", `${view.viewer_role} · clearance ${view.viewer_clearance}`],
    ["As of", view.as_of || "—"],
    ["Visible items", String(view.stats.claims + view.stats.conflicts + view.stats.questions)],
    ["Withheld", withheldText],
    ["View fingerprint", view.view_sha256.slice(0, 16) + "…"],
  ]) {
    const line = makeElement("div");
    line.append(makeElement("dt", "", label), makeElement("dd", "", value));
    facts.append(line);
  }
}

async function acknowledgeHandover() {
  if (!state.handover) return;
  const button = $("#acknowledge-button");
  button.disabled = true;
  const visible = state.handover.stats.claims + state.handover.stats.conflicts + state.handover.stats.questions;
  const withheld = Object.values(state.handover.withheld || {}).reduce((sum, n) => sum + n, 0);
  try {
    const result = await api("/api/handover/acknowledge", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Baton-Client": "web" },
      body: JSON.stringify({ view_sha256: state.handover.view_sha256, visible_count: visible, withheld_count: withheld }),
    });
    $("#handover-ack-body").innerHTML = `<div class="ack-signed"><strong>Handover acknowledged</strong>
      Signed ${formatDateTime(result.acknowledged_at)} against fingerprint ${state.handover.view_sha256.slice(0, 12)}…</div>`;
    showToast("Handover acknowledged and recorded.", "success");
  } catch (error) {
    showToast(error.status === 409 ? "The handover view changed since it was shown. Reload and acknowledge again." : error.message, "error");
  } finally {
    button.disabled = false;
  }
}

/* --- audit -------------------------------------------------------------------------------------- */

function renderAudit(container, events) {
  const body = $("#audit-tbody");
  body.replaceChildren();
  $("#audit-total").textContent = String(events.length);
  if (!events.length) {
    body.append(Object.assign(document.createElement("tr"), { innerHTML: `<td colspan="5"><div class="search-empty">No events recorded yet.</div></td>` }));
    return;
  }
  for (const event of events.slice(0, 100)) {
    const row = document.createElement("tr");
    const when = makeElement("td", "", formatDateTime(event.created_at));
    const actor = makeElement("td", "", event.actor_label);
    const action = makeElement("td");
    action.innerHTML = `<span class="review-chip review-proposed">${escapeHtml(event.action)}</span>`;
    const target = makeElement("td", "", event.target_type ? `${event.target_type}${event.target_id ? ` #${event.target_id}` : ""}` : "—");
    const detail = makeElement("td", "detail-cell", event.detail ? Object.entries(event.detail).map(([k, v]) => `${k}: ${v}`).join(" · ") : "—");
    row.append(when, actor, action, target, detail);
    body.append(row);
  }
}

/* --- view switching + wiring -------------------------------------------------------------------- */

const VIEW_LOADERS = {
  extraction: () => loadJobs(),
  claims: () => loadClaims(),
  review: async () => { await loadClaims(); renderReviewQueue($("#review-queue")); },
  conflicts: () => loadInto("#conflict-list", () => api("/api/handover").then(v => v.conflicts), renderConflicts),
  questions: () => loadQuestions(),
  assistant: () => {},
  handover: () => loadHandover(),
  audit: () => loadInto("#audit-tbody", () => api("/api/audit?limit=100"), renderAudit),
};

const originalSetView = setView;
setView = function (view) {
  originalSetView(view);
  $("#breadcrumb-current").textContent = VIEW_TITLES[view] || view;
  if (VIEW_LOADERS[view] && !state.loaded.has(view)) {
    state.loaded.add(view);
    VIEW_LOADERS[view]();
  }
};

$("#claim-filter").addEventListener("input", renderClaims);
$("#run-pipeline-button").addEventListener("click", () => runPipeline());
$("#refresh-jobs-button").addEventListener("click", loadJobs);
$("#acknowledge-button").addEventListener("click", acknowledgeHandover);
$("#chat-form").addEventListener("submit", askAssistant);
$("#claim-dialog-close").addEventListener("click", () => $("#claim-dialog").close());
$("#claim-dialog").addEventListener("click", event => {
  if (event.target === $("#claim-dialog")) $("#claim-dialog").close();
});
// data-retry handled inline by loadInto
$("#theme-toggle").addEventListener("click", () => {
  state.theme = state.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = state.theme;
  try { localStorage.setItem("baton-theme", state.theme); } catch { /* storage unavailable */ }
});
