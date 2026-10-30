import { DocumentStore, isBusy, isFixing, defaultFilters } from './documents.mjs';
import { $, element, notice, renderReport, renderCoverage } from './views.mjs?v=20261031';

let storage;
try { storage = window.localStorage; } catch { storage = null; }
const store = new DocumentStore({ storage, changed: render });
let selectedKey = null;
let selectedFile = null;
let mode = 'landing';
let uploadError = '';
let navigationSequence = 0;
const mobile = window.matchMedia('(max-width: 650px)');
const current = () => store.documents.get(selectedKey);

function navigate(view, entry = null, push = true) {
  navigationSequence++;
  mode = view; selectedKey = entry?.key || null;
  const url = new URL(location.href); url.searchParams.delete('document'); url.hash = '';
  if (view === 'upload') url.hash = 'check';
  if (view === 'report' && entry?.data?.document_id) url.searchParams.set('document', entry.data.document_id);
  else if (view === 'report' && entry) url.hash = `pending=${entry.key}`;
  history[push ? 'pushState' : 'replaceState'](null, '', url);
  render();
  window.scrollTo({ top: 0, behavior: 'instant' });
  if (view !== 'landing') $(view === 'upload' ? 'upload-title' : 'document-title').focus({ preventScroll: true });
}
async function restoreRoute() {
  const sequence = ++navigationSequence;
  const url = new URL(location.href), id = url.searchParams.get('document');
  if (id) {
    mode = 'report';
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
      mode = 'upload'; selectedKey = null; uploadError = 'В адресе указан некорректный идентификатор документа. Выберите DOCX заново.'; render(); return;
    }
    const entry = await store.open(id);
    if (sequence !== navigationSequence) return;
    selectedKey = entry.key; render();
    if (store.session) await store.refresh(entry);
    return;
  }
  const pending = url.hash.startsWith('#pending=') ? store.documents.get(url.hash.slice(9)) : null;
  selectedKey = pending?.key || null;
  mode = pending ? 'report' : url.hash === '#check' || url.hash.startsWith('#pending=') ? 'upload' : 'landing';
  render();
  if (mode === 'landing' && url.hash) document.getElementById(url.hash.slice(1))?.scrollIntoView();
}
function choose(file) {
  if (!file) return;
  uploadError = store.validate(file);
  selectedFile = uploadError ? null : file;
  if (mode !== 'upload') navigate('upload'); else render();
}
function start(file = selectedFile) {
  try {
    const entry = store.upload(file); selectedFile = null; uploadError = ''; navigate('report', entry);
  } catch (error) { uploadError = error.message; render(); }
}
function render() {
  const entry = current(), data = entry?.data;
  $('landing').hidden = mode !== 'landing'; $('application').hidden = mode === 'landing';
  $('application').classList.toggle('report-visible', mode === 'report' && Boolean(data?.report));
  $('upload-screen').hidden = mode !== 'upload'; $('workspace').hidden = mode !== 'report';
  $('new-document').hidden = mode !== 'report'; $('standard-badge').hidden = mode === 'report';
  $('app-context').textContent = mode === 'report' && entry ? `${entry.filename} / СТП 01–2017` : 'Проверка оформления';
  const selectionError = mode === 'upload' ? uploadError || (selectedFile ? store.validate(selectedFile) : '') : '';
  notice('global-message', selectionError || store.sessionError, true);
  $('session-retry').hidden = !store.sessionError;
  notice('pause-message', store.paused ? 'Новые загрузки и автоисправление временно приостановлены по ограничению сервера. После паузы повторите действие вручную. Просмотр и скачивание доступны.' : '');
  notice('busy-message', store.session && store.activeCount >= store.session.max_concurrent_jobs && !store.paused ? 'Обработчики заняты документами этой вкладки. Новую проверку или исправление можно запустить после завершения одной из операций. Просмотр отчётов доступен.' : '');
  $('file-input').disabled = false;
  $('upload-limit').textContent = store.session ? `DOCX · до ${new Intl.NumberFormat('ru', { maximumFractionDigits: 1 }).format(store.session.max_upload_bytes / 1048576)} МБ` : 'DOCX · Лимит будет доступен после подключения';
  $('selected-filename').hidden = !selectedFile; $('selected-filename').textContent = selectedFile?.name || '';
  $('upload-label').textContent = selectedFile ? 'Сменить файл' : 'Выбрать DOCX';
  $('upload-subtitle').textContent = selectedFile ? 'Выберите другой DOCX или перетащите его сюда' : 'или перетащите документ сюда';
  $('start-check').disabled = !selectedFile || Boolean(store.validate(selectedFile)) || Boolean(store.admissionError());
  const entries = [...store.documents.values()];
  $('document-switcher').hidden = !entries.length || (entries.length === 1 && mode === 'report');
  const placeholder = element('option', '', 'Открыть документ'); placeholder.value = '';
  $('document-select').replaceChildren(placeholder, ...entries.map(item => { const option = element('option', '', item.filename + (isBusy(item) ? ' · обработка' : item.error ? ' · ошибка' : '')); option.value = item.key; return option; }));
  $('document-select').value = selectedKey || '';
  if (!entry || mode !== 'report') return;
  if (data?.document_id && new URL(location.href).searchParams.get('document') !== data.document_id) {
    const url = new URL(location.href); url.hash = ''; url.searchParams.set('document', data.document_id); history.replaceState(null, '', url);
  }
  const busy = isBusy(entry), fixing = isFixing(entry), corrected = data?.current_version === 'corrected', report = data?.report;
  $('document-title').textContent = fixing ? 'Исправляем и повторно проверяем' : report ? corrected ? 'Отчёт исправленной копии' : 'Отчёт исходного документа' : busy ? 'Проверяем документ' : 'Отчёт пока недоступен';
  $('document-state').textContent = fixing ? 'Предыдущий отчёт сохранён. Результат появится после завершения всей операции.' : entry.refreshing ? 'Обновляем статус…' : corrected ? 'Поддерживаемые исправления применены. Копия повторно проверена.' : '';
  notice('document-message', entry.error || data?.last_error?.message || '', true);
  $('report').hidden = !report; $('report-counts').hidden = !report;
  $('waiting').hidden = Boolean(report); $('waiting-spinner').hidden = !busy;
  $('waiting-title').textContent = busy ? 'Проверяем оформление по СТП' : 'Проверка не дала доступного отчёта';
  $('waiting-filename').textContent = entry.filename;
  $('waiting-note').textContent = busy ? 'Результат появится после завершения проверки. Исходный файл не меняется. Можно работать с другими документами.' : 'Используйте действие ниже или выберите другой DOCX. Автоматического повтора загрузки нет.';
  $('refresh').hidden = !data || (!entry.error && !busy && data.state !== 'failed');
  $('refresh').disabled = Boolean(entry.refreshing || entry.operation || !store.session);
  $('retry-upload').hidden = !entry.file || busy;
  $('retry-upload').disabled = Boolean(store.admissionError());
  $('choose-again').hidden = busy || Boolean(report);
  $('recovery-actions').hidden = ['refresh', 'retry-upload', 'choose-again'].every(id => $(id).hidden);
  if (!report) return;
  $('actions-title').textContent = fixing ? 'Исправляем и проверяем' : corrected ? 'Исправленная копия' : entry.error ? 'Автоисправление не завершено' : report.findings.length ? 'Исправить оформление' : 'Получить проверенную копию';
  $('actions-description').textContent = corrected ? 'Поддерживаемые правки применены. Ниже актуальный результат повторной проверки.' : fixing ? 'Дождитесь завершения операции. Исходный отчёт и его отметки сохранены.' : 'Поддерживаемые правки применятся ко всему документу, не к карточке.';
  $('fix-spinner').hidden = !fixing;
  $('fix').disabled = busy || corrected || entry.refreshing || Boolean(store.admissionError());
  $('fix').textContent = fixing ? 'Операция выполняется…' : corrected ? 'Копия уже обработана' : !report.findings.length ? 'Создать проверенную копию' : 'Автоисправить документ';
  $('action-note').textContent = corrected ? 'Оставшиеся замечания проверьте в Word. После ручных правок загрузите документ заново.' : 'Исходный файл не меняется. После автоисправления копия проходит повторную проверку и становится доступна для скачивания.';
  $('download').disabled = !data.download_url || entry.downloading;
  $('download').textContent = entry.downloading ? 'Получаем файл…' : 'Скачать исправленную копию';
  $('download-note').textContent = data.download_url ? 'Копия готова. Оставшиеся замечания не мешают скачиванию.' : 'Доступно после повторной проверки. Оставшиеся замечания не мешают скачиванию.';
  $('download-status').textContent = entry.downloaded ? '✓ Скачивание началось' : '';
  renderReport(entry, { mobile: mobile.matches, onSelect: index => { entry.selectedFinding = index; render(); }, onMark: (index, checked) => store.mark(entry, index, checked) });
  const coverageKey = JSON.stringify([entry.key, data.warnings, data.limitations]);
  if ($('coverage')._key !== coverageKey) { renderCoverage(data); $('coverage')._key = coverageKey; }
}
async function download() {
  const entry = current();
  if (!entry?.data?.download_url || entry.downloading) return;
  entry.downloading = true; entry.downloaded = false; entry.error = ''; render();
  try {
    const { blob, name } = await store.api.download(entry.data), url = URL.createObjectURL(blob), link = element('a');
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000); entry.downloaded = true;
  } catch (error) { await store.error(entry, error); }
  finally { entry.downloading = false; render(); }
}
function resetFilters() { const entry = current(); if (entry) { entry.filters = defaultFilters(); render(); } }
for (const node of document.querySelectorAll('[data-check]')) node.addEventListener('click', event => { event.preventDefault(); uploadError = ''; navigate('upload'); });
for (const node of document.querySelectorAll('[data-home]')) node.addEventListener('click', event => { event.preventDefault(); navigate('landing'); });
$('file-input').addEventListener('change', event => { choose(event.target.files[0]); event.target.value = ''; });
$('start-check').addEventListener('click', () => start());
$('retry-upload').addEventListener('click', () => start(current()?.file));
$('session-retry').addEventListener('click', async () => { if (await store.connect()) await restoreRoute(); });
$('document-select').addEventListener('change', event => { const entry = store.documents.get(event.target.value); if (entry) navigate('report', entry); });
$('fix').addEventListener('click', () => store.fix(current())); $('download').addEventListener('click', download);
$('refresh').addEventListener('click', () => store.refresh(current()));
$('search').addEventListener('input', event => { if (current()) { current().filters.query = event.target.value; render(); } });
$('clear-search').addEventListener('click', () => { if (current()) { current().filters.query = ''; render(); $('search').focus(); } });
$('category').addEventListener('change', event => { if (current()) { current().filters.category = event.target.value; render(); } });
$('status').addEventListener('change', event => { if (current()) { current().filters.status = event.target.value; render(); } });
$('hide-done').addEventListener('change', event => { if (current()) { current().filters.hideDone = event.target.checked; render(); } });
$('reset-filters').addEventListener('click', resetFilters);
$('empty-action').addEventListener('click', () => { if ($('empty-action').dataset.action === 'show' && current()) { current().filters.hideDone = false; render(); } else resetFilters(); $('search').focus(); });
$('show-coverage').addEventListener('click', () => { $('coverage').open = true; $('coverage').querySelector('summary').focus(); $('coverage').scrollIntoView({ block: 'nearest', behavior: 'smooth' }); });
for (const id of ['upload-zone', 'hero']) {
  const zone = $(id); let depth = 0;
  zone.addEventListener('dragenter', event => { if (!event.dataTransfer.types.includes('Files')) return; event.preventDefault(); depth++; zone.classList.add('dragging'); });
  zone.addEventListener('dragover', event => event.preventDefault());
  zone.addEventListener('dragleave', () => { if (--depth <= 0) zone.classList.remove('dragging'); });
  zone.addEventListener('drop', event => {
    event.preventDefault(); depth = 0; zone.classList.remove('dragging');
    if (event.dataTransfer.files.length !== 1) { uploadError = 'Перетащите один DOCX-файл. Следующий можно загрузить отдельно.'; navigate('upload'); return; }
    choose(event.dataTransfer.files[0]);
  });
}
window.addEventListener('dragover', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
window.addEventListener('drop', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
window.addEventListener('popstate', restoreRoute); window.addEventListener('hashchange', restoreRoute);
mobile.addEventListener('change', render);
await restoreRoute();
if (await store.connect()) {
  const entry = current();
  if (mode === 'report' && entry?.data?.document_id) await store.refresh(entry);
}

$('example-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText('Система выполняет проверку документации…'); $('announcement').textContent = 'Фрагмент примера скопирован'; }
  catch { $('announcement').textContent = 'Копирование недоступно. Выделите текст примера вручную.'; }
});
