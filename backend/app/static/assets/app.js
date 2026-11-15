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
  panel.hidden = !state.demoMode;
  state.demoCredentials = null;
  if (!state.demoMode) return;

  try {
    state.demoCredentials = await api("/api/auth/demo-credentials");
    $("#demo-email").textContent = state.demoCredentials.email;
    $("#demo-access-message").hidden = true;
    $("#demo-fill-button").disabled = false;
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

$("#demo-fill-button").addEventListener("click", () => {
  if (!state.demoCredentials) return;
  $("#email").value = state.demoCredentials.email;
  $("#password").value = state.demoCredentials.password;
  $("#email").focus();
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
