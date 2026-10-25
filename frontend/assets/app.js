import { STATUSES, RULES, PROPERTIES, filteredFindings, markStorageKey, parseMarks, readResponse, retryDelay, operationErrorMessage } from "./report.mjs";

const $ = id => document.getElementById(id);
const documents = new Map();
let selected = null;
let session = null;
let localId = 0;
let mutationRetryUntil = 0;
let retryTimer;
let pauseNotice = false;
const filters = { status: "all", category: "all", query: "", hideDone: false };
const isBusy = entry => entry?.operation || ["checking", "fixing"].includes(entry?.data?.state);
const current = () => documents.get(selected);
const apiPath = entry => `/api/documents/${encodeURIComponent(entry.data.document_id)}`;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function message(id, text, error = false) {
  if (id === "global-message") pauseNotice = false;
  $(id).textContent = text || "";
  $(id).hidden = !text;
  $(id).classList.toggle("error-notice", error);
}

async function request(path, init = {}) {
  const headers = new Headers(init.headers);
  if (init.method === "POST") headers.set("X-CSRF-Token", session.csrf_token);
  return readResponse(await fetch(path, { ...init, headers, credentials: "same-origin" }));
}

async function connect() {
  $("session-retry").hidden = true;
  try {
    session = await request("/api/session");
    $("upload-limit").textContent = `DOCX · до ${new Intl.NumberFormat("ru", { maximumFractionDigits: 1 }).format(session.max_upload_bytes / 1048576)} МБ`;
    message("global-message", "");
    render();
    return true;
  } catch {
    session = null;
    message("global-message", "Не удалось подключиться к серверу. Проверьте соединение и повторите подключение.", true);
    $("session-retry").hidden = false;
    $("upload-limit").textContent = "DOCX · Нет подключения";
    render();
    return false;
  }
}

function resetFilters() {
  Object.assign(filters, { status: "all", category: "all", query: "", hideDone: false });
  $("search").value = "";
  $("hide-done").checked = false;
  $("category").value = "all";
}

function select(key, updateURL = true) {
  selected = key;
  resetFilters();
  if (updateURL) {
    const url = new URL(location.href);
    const id = current()?.data?.document_id;
    if (id) url.searchParams.set("document", id);
    else url.searchParams.delete("document");
    history.replaceState(null, "", url);
  }
  render();
}

async function adopt(entry, data) {
  entry.data = data;
  entry.filename = data.filename;
  if (data.report) {
    try {
      const key = await markStorageKey(data);
      if (key !== entry.markKey) {
        entry.markKey = key;
        entry.marks = parseMarks(localStorage.getItem(key), data.report.findings.length);
      }
    } catch {
      entry.markKey = null;
      entry.marks = new Set();
      entry.storageUnavailable = true;
    }
  }
  if (selected === entry.key) {
    const url = new URL(location.href);
    url.searchParams.set("document", data.document_id);
    history.replaceState(null, "", url);
  }
  render();
}

function newEntry(key, filename) {
  const entry = { key, filename, data: null, operation: null, error: "", marks: new Set(), markKey: null, timer: null };
  documents.set(key, entry);
  return entry;
}

async function handleError(entry, error, upload = false) {
  entry.error = operationErrorMessage(error);
  if (error instanceof TypeError) {
    entry.error = upload
      ? "Соединение прервалось. Документ мог сохраниться на сервере. Повторная загрузка создаст ещё одну копию; автоматического повтора нет."
      : "Соединение прервалось. Обновите статус, чтобы узнать результат операции.";
  }
  if (error.status === 404) {
    entry.error = "Документ недоступен в этой сессии. Откройте его в прежнем браузере или загрузите файл заново. Это не означает удаления файла с сервера.";
    entry.data = null;
  }
  if (error.status === 401 || error.status === 403) {
    if (error.status === 401) entry.data = null;
    const connected = await connect();
    entry.error = connected
      ? "Сессия обновлена. Повторите действие вручную. Если прежняя cookie потеряна, доступ к старому документу не восстановится."
      : "Не удалось обновить сессию. Повторите подключение к серверу.";
  }
  if ([429, 503].includes(error.status)) {
    const seconds = retryDelay(error);
    entry.error += ` Повторите действие через ${seconds} сек.`;
    entry.retryUntil = Date.now() + seconds * 1000;
    mutationRetryUntil = Math.max(mutationRetryUntil, entry.retryUntil);
    scheduleRetryRender();
  }
}

function scheduleRetryRender() {
  clearTimeout(retryTimer);
  const remaining = mutationRetryUntil - Date.now();
  if (remaining <= 0) return render();
  retryTimer = setTimeout(() => { render(); scheduleRetryRender(); }, Math.min(remaining + 20, 2147483647));
}

function mutationPaused() {
  if (mutationRetryUntil <= Date.now()) return false;
  const seconds = Math.ceil((mutationRetryUntil - Date.now()) / 1000);
  message("global-message", `Новые операции временно приостановлены. Повторите действие через ${seconds} сек.`, true);
  pauseNotice = true;
  return true;
}

async function upload(file) {
  if (!session || !file) return;
  if (mutationPaused()) return;
  if (!file.name.toLowerCase().endsWith(".docx")) return message("global-message", "Выберите документ в формате DOCX.", true);
  if (!file.size) return message("global-message", "Файл пуст. Выберите другой документ.", true);
  if (file.size > session.max_upload_bytes) return message("global-message", `Файл слишком большой. ${$("upload-limit").textContent}.`, true);
  if ([...documents.values()].filter(isBusy).length >= session.max_concurrent_jobs) {
    return message("global-message", "Доступные обработчики заняты вашими документами. Дождитесь завершения одной из операций.", true);
  }
  message("global-message", "");
  const entry = newEntry(`upload-${++localId}`, file.name);
  entry.operation = "checking";
  select(entry.key);
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    await adopt(entry, await request("/api/documents", { method: "POST", body: form }));
  } catch (error) { await handleError(entry, error, true); }
  finally { entry.operation = null; render(); }
}

async function refresh(entry, poll = false) {
  if (!entry?.data || entry.refreshing) return;
  clearTimeout(entry.timer);
  entry.refreshing = true;
  if (!poll) entry.error = "";
  render();
  try {
    await adopt(entry, await request(apiPath(entry)));
    entry.error = "";
    if (["checking", "fixing"].includes(entry.data.state)) {
      entry.timer = setTimeout(() => refresh(entry, true), 1800);
    }
  } catch (error) { await handleError(entry, error); }
  finally { entry.refreshing = false; render(); }
}

async function fix() {
  const entry = current();
  if (!entry?.data || isBusy(entry) || entry.refreshing) return;
  if (mutationPaused() || entry.retryUntil > Date.now()) return;
  entry.operation = "fixing";
  entry.error = "";
  render();
  try {
    // Resolve an earlier interrupted request before starting another mutation.
    await adopt(entry, await request(apiPath(entry)));
    if (entry.data.current_version === "corrected") return;
    if (["checking", "fixing"].includes(entry.data.state)) {
      entry.timer = setTimeout(() => refresh(entry, true), 1800);
      return;
    }
    await adopt(entry, await request(`${apiPath(entry)}/fix`, { method: "POST" }));
  } catch (error) {
    await handleError(entry, error);
    if (entry.data && ![401, 403, 404].includes(error.status)) {
      try {
        await adopt(entry, await request(apiPath(entry)));
        if (entry.data.current_version === "corrected") entry.error = "";
        if (["checking", "fixing"].includes(entry.data.state)) entry.timer = setTimeout(() => refresh(entry, true), 1800);
      } catch (recoveryError) {
        if ([401, 403, 404].includes(recoveryError.status)) await handleError(entry, recoveryError);
      }
    }
  } finally { entry.operation = null; render(); }
}

async function download() {
  const entry = current();
  if (!entry?.data?.download_url || entry.downloading) return;
  entry.downloading = true;
  entry.error = "";
  render();
  try {
    const response = await fetch(entry.data.download_url, { credentials: "same-origin" });
    if (!response.ok) await readResponse(response);
    const disposition = response.headers.get("Content-Disposition") || "";
    const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const name = encodedName ? decodeURIComponent(encodedName) : "исправлено.docx";
    const url = URL.createObjectURL(await response.blob());
    const link = element("a");
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) { await handleError(entry, error); }
  finally { entry.downloading = false; render(); }
}

function render() {
  const entry = current();
  document.body.classList.toggle("has-document", Boolean(entry));
  $("workspace").hidden = !entry;
  const paused = mutationRetryUntil > Date.now();
  if (!paused && pauseNotice) message("global-message", "");
  $("file-input").disabled = !session || paused;
  $("upload-label").textContent = entry ? "Другой документ" : "Выбрать документ";
  $("upload-title").textContent = entry ? "Проверить другой документ" : "Начнём с вашего документа";
  $("upload-subtitle").textContent = paused
    ? "Приём новых операций временно приостановлен. Дождитесь окончания паузы."
    : entry ? "Можно загрузить новую версию после ручных правок" : "Перетащите файл сюда или выберите его на устройстве";
  if (!entry) return;
  $("document-switcher").hidden = documents.size < 2;
  $("document-select").replaceChildren(...[...documents.values()].map(item => {
    const option = element("option", "", item.filename + (isBusy(item) ? " · обработка" : ""));
    option.value = item.key;
    option.selected = item.key === selected;
    return option;
  }));
  const data = entry.data;
  const busy = isBusy(entry);
  const corrected = data?.current_version === "corrected";
  $("filename").textContent = entry.filename;
  $("document-state").classList.toggle("busy", Boolean(busy));
  $("document-state").textContent = busy
    ? ((entry.operation === "fixing" || data?.state === "fixing") ? "Исправляем и повторно проверяем…" : "Проверяем документ…")
    : entry.refreshing ? "Обновляем статус…" : corrected ? "Исправленная копия · проверена повторно" : data?.report ? "Исходный документ · проверен" : "Отчёт пока недоступен";
  $("fix").hidden = !data?.report || corrected;
  $("fix").disabled = Boolean(busy || entry.refreshing || !session || paused || entry.retryUntil > Date.now());
  $("download").hidden = !data?.download_url;
  $("download").disabled = Boolean(entry.downloading);
  $("refresh").hidden = !data || (!entry.error && !busy && data.state !== "failed");
  $("refresh").disabled = Boolean(entry.refreshing || entry.operation);
  message("document-message", entry.error || data?.last_error?.message || "", true);
  $("action-note").textContent = corrected
    ? "Поддерживаемые исправления применены. Ниже актуальный отчёт. Оставшиеся замечания проверьте в Word; после ручных правок загрузите файл заново."
    : "Автоисправление создаст отдельную копию и проверит её заново. Часть замечаний потребуется разобрать вручную.";
  if (entry.storageUnavailable) $("action-note").textContent += " Браузер не разрешил сохранить отметки: после обновления страницы они сбросятся.";
  $("report").hidden = !data?.report;
  if (data?.report) {
    const categories = [...new Set(data.report.findings.map(finding => finding.rule_id))].sort();
    if (!categories.includes(filters.category)) filters.category = "all";
    const all = element("option", "", "Все категории");
    all.value = "all";
    $("category").replaceChildren(all, ...categories.map(rule => {
      const option = element("option", "", `${rule} · ${RULES[rule] || "Другое правило"}`);
      option.value = rule;
      return option;
    }));
    $("category").value = filters.category;
    renderFindings();
  }
  $("coverage").hidden = !data;
  $("warning-count").textContent = data?.warnings?.length ? `Особенности документа: ${data.warnings.length}` : "";
  $("warnings").replaceChildren();
  $("limitations").replaceChildren();
  if (data?.warnings?.length) {
    const list = element("ul");
    data.warnings.forEach(warning => list.append(element("li", "", warning)));
    $("warnings").append(element("h3", "", "Особенности документа"), list);
  }
  if (data?.limitations?.length) {
    const list = element("ul");
    data.limitations.forEach(item => list.append(element("li", "", `${item.rule_id} — ${item.description}`)));
    $("limitations").append(element("h3", "", "Границы проверок"), list);
  }
}

function renderFindings() {
  const entry = current();
  if (!entry?.data?.report) return;
  const findings = entry.data.report.findings;
  $("count-all").textContent = findings.length;
  for (const status of Object.keys(STATUSES)) $("count-" + status).textContent = findings.filter(finding => finding.status === status).length;
  document.querySelectorAll("[data-status]").forEach(button => {
    const active = button.dataset.status === filters.status;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  updateMarkedCount(entry);
  const visible = filteredFindings(findings, filters, entry.marks);
  $("visible-count").textContent = `Показано ${visible.length} из ${findings.length}`;
  $("findings").replaceChildren(...visible.map(({ finding, index }) => findingCard(entry, finding, index)));
  $("empty").hidden = visible.length > 0;
  $("empty-title").textContent = findings.length ? "Здесь пока пусто" : "В пределах выполненных проверок замечаний не найдено";
  $("empty-text").textContent = findings.length
    ? "По выбранным фильтрам нет замечаний. Измените запрос или покажите отмеченные пункты."
    : "Проверка охватывает часть требований СТП. Перед сдачей проверьте окончательную вёрстку в Word.";
  $("reset-filters").hidden = !findings.length;
}

function updateMarkedCount(entry) {
  $("marked-count").textContent = `Отмечено вручную: ${entry.marks.size} из ${entry.data.report.findings.length}`;
}

function findingCard(entry, finding, index) {
  const card = element("article", "finding");
  card.classList.toggle("is-marked", entry.marks.has(index));
  const header = element("div", "finding-header");
  const meta = element("div", "finding-meta");
  meta.append(element("span", `badge ${finding.status}`, STATUSES[finding.status] || finding.status),
    element("span", "rule", `${finding.rule_id} · ${RULES[finding.rule_id] || "Другое правило"}`));
  const label = element("label", "mark");
  const checkbox = element("input");
  checkbox.type = "checkbox";
  checkbox.checked = entry.marks.has(index);
  checkbox.setAttribute("aria-label", `Исправлено вручную: ${finding.actual}`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) entry.marks.add(index); else entry.marks.delete(index);
    try {
      if (entry.markKey) localStorage.setItem(entry.markKey, JSON.stringify([...entry.marks]));
    } catch { entry.storageUnavailable = true; render(); return; }
    card.classList.toggle("is-marked", checkbox.checked);
    updateMarkedCount(entry);
    if (filters.hideDone) {
      renderFindings();
      $("hide-done").focus();
    }
  });
  label.append(checkbox, element("span", "", "Исправлено"));
  header.append(meta, label);
  card.append(header, element("h3", "", finding.actual));
  const expected = element("p", "expected");
  expected.append(element("span", "expected-label", "Как должно быть: "), document.createTextNode(finding.expected));
  card.append(expected);
  for (const location of finding.locations) {
    const block = element("div", "location");
    const where = [location.section || "В документе", PROPERTIES[location.property] || location.property].filter(Boolean).join(" · ");
    block.append(element("p", "location-heading", where));
    if (location.fragment_start) {
      block.append(element("p", "fragment", location.fragment_start));
      const copy = element("button", "copy", "Копировать фрагмент");
      copy.type = "button";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(location.fragment_start);
          copy.textContent = "Скопировано";
        } catch { copy.textContent = "Выделите и скопируйте текст вручную"; }
        setTimeout(() => { copy.textContent = "Копировать фрагмент"; }, 2500);
      });
      block.append(copy);
    }
    if (location.context) block.append(element("p", "context", location.context));
    card.append(block);
  }
  card.append(element("p", "reference", `Основание: ${finding.standard_reference}`));
  return card;
}

async function restoreFromURL() {
  const id = new URL(location.href).searchParams.get("document");
  if (!id) return select(null, false);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    message("global-message", "В адресе указан некорректный идентификатор документа. Загрузите DOCX заново.", true);
    return;
  }
  const entry = [...documents.values()].find(item => item.data?.document_id === id) || newEntry(id, "Открываем документ…");
  entry.data ||= { document_id: id, state: "checking" };
  select(entry.key, false);
  await refresh(entry);
}

$("session-retry").addEventListener("click", async () => { if (await connect()) await restoreFromURL(); });
$("file-input").addEventListener("change", event => {
  const file = event.target.files[0];
  event.target.value = "";
  upload(file);
});
let dragDepth = 0;
$("upload-zone").addEventListener("dragenter", event => { event.preventDefault(); dragDepth++; $("upload-zone").classList.add("dragging"); });
$("upload-zone").addEventListener("dragover", event => event.preventDefault());
$("upload-zone").addEventListener("dragleave", () => { if (--dragDepth <= 0) $("upload-zone").classList.remove("dragging"); });
$("upload-zone").addEventListener("drop", event => {
  event.preventDefault();
  dragDepth = 0;
  $("upload-zone").classList.remove("dragging");
  if (event.dataTransfer.files.length !== 1) return message("global-message", "Перетащите один DOCX-файл. Следующий можно загрузить отдельно.", true);
  upload(event.dataTransfer.files[0]);
});
// Dropping outside the target must not navigate away from a pending document.
window.addEventListener("dragover", event => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); });
window.addEventListener("drop", event => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); });
$("document-select").addEventListener("change", event => select(event.target.value));
$("fix").addEventListener("click", fix);
$("download").addEventListener("click", download);
$("refresh").addEventListener("click", () => refresh(current()));
$("search").addEventListener("input", event => { filters.query = event.target.value; renderFindings(); });
$("category").addEventListener("change", event => { filters.category = event.target.value; renderFindings(); });
$("hide-done").addEventListener("change", event => { filters.hideDone = event.target.checked; renderFindings(); });
document.querySelectorAll("[data-status]").forEach(button => button.addEventListener("click", () => { filters.status = button.dataset.status; renderFindings(); }));
$("reset-filters").addEventListener("click", () => { resetFilters(); renderFindings(); });
window.addEventListener("popstate", restoreFromURL);
if (await connect()) await restoreFromURL();
