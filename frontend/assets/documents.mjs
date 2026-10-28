import { DocumentAPI } from './api.mjs';
import { markStorageKey, parseMarks, retryDelay, operationErrorMessage } from './report.mjs';

export const defaultFilters = () => ({ status: 'all', category: 'all', query: '', hideDone: false });
export const isBusy = entry => Boolean(entry?.operation || ['checking', 'fixing'].includes(entry?.data?.state));
export const isFixing = entry => entry?.operation === 'fixing' || entry?.data?.state === 'fixing';

export class DocumentStore {
  documents = new Map();
  pausedUntil = 0;
  sessionError = '';
  sequence = 0;
  constructor({ api = new DocumentAPI(), changed = () => {}, storage = globalThis.localStorage } = {}) {
    this.api = api;
    this.changed = changed;
    this.storage = storage;
  }
  get session() { return this.api.session; }
  get paused() { return this.pausedUntil > Date.now(); }
  get activeCount() { return [...this.documents.values()].filter(isBusy).length; }
  async connect() {
    if (this.connecting) return this.connecting;
    this.connecting = (async () => {
      try { await this.api.connect(); this.sessionError = ''; return true; }
      catch { this.api.session = null; this.sessionError = 'Не удалось подключиться к серверу. Проверьте соединение и повторите подключение.'; return false; }
      finally { this.changed(); }
    })();
    try { return await this.connecting; } finally { this.connecting = null; }
  }
  validate(file) {
    if (!file) return 'Выберите документ для проверки.';
    if (!/\.docx$/i.test(file.name)) return 'Нужен файл DOCX. PDF, DOC и скан-копии не подходят.';
    if (!file.size) return 'Файл пуст. Выберите другой документ.';
    if (this.session && file.size > this.session.max_upload_bytes) return `Файл слишком большой. Допустимо до ${new Intl.NumberFormat('ru', { maximumFractionDigits: 1 }).format(this.session.max_upload_bytes / 1048576)} МБ.`;
    return '';
  }
  admissionError() {
    if (!this.session) return 'Нет подключения к серверу. Повторите подключение.';
    if (this.paused) return 'Новые операции временно приостановлены. Дождитесь окончания паузы и повторите действие вручную.';
    if (this.activeCount >= this.session.max_concurrent_jobs) return 'Доступные обработчики заняты вашими документами. Дождитесь завершения одной из операций.';
    return '';
  }
  entry(key, filename) {
    const entry = { key, filename, data: null, operation: null, error: '', marks: new Set(), markKey: null,
      reportScope: null, filters: defaultFilters(), selectedFinding: null, expanded: new Set(), timer: null };
    this.documents.set(key, entry);
    return entry;
  }
  async adopt(entry, data) {
    const scope = data.report ? JSON.stringify([data.document_id, data.current_version, data.checker_version, data.ruleset_version, data.report]) : null;
    if (scope !== entry.reportScope) {
      entry.marks = new Set(); entry.markKey = null; entry.selectedFinding = null; entry.expanded.clear();
      entry.filters = defaultFilters(); entry.storageUnavailable = false;
      if (data.report) {
        try {
          entry.markKey = await markStorageKey(data);
          entry.marks = parseMarks(this.storage.getItem(entry.markKey), data.report.findings.length);
        } catch { entry.storageUnavailable = true; }
      }
      entry.reportScope = scope;
    }
    entry.data = data; entry.filename = data.filename; this.changed();
  }
  mark(entry, index, checked) {
    if (isFixing(entry) || !entry.data?.report || !Number.isInteger(index) || index < 0 || index >= entry.data.report.findings.length) return;
    if (checked) entry.marks.add(index); else entry.marks.delete(index);
    try {
      if (!entry.markKey) throw new Error('No persistent report scope');
      this.storage.setItem(entry.markKey, JSON.stringify([...entry.marks]));
    } catch { entry.storageUnavailable = true; }
    this.changed();
  }
  async error(entry, error, upload = false) {
    entry.error = operationErrorMessage(error);
    if (error instanceof TypeError) entry.error = upload
      ? 'Соединение прервалось. Документ мог сохраниться на сервере. Повторная загрузка создаст ещё одну копию. Автоматического повтора нет.'
      : 'Соединение прервалось. Обновите статус, чтобы узнать результат операции.';
    if ([401, 404].includes(error.status)) {
      entry.data = null; entry.marks.clear(); entry.reportScope = null; entry.markKey = null;
    }
    if (error.status === 404) entry.error = 'Документ недоступен в этой сессии. Откройте его в прежнем браузере или загрузите файл заново. Это не означает удаления файла с сервера.';
    if ([401, 403].includes(error.status)) {
      const connected = await this.connect();
      entry.error = connected ? 'Сессия обновлена. Повторите действие вручную. Потеря прежней cookie не позволяет восстановить доступ к старому документу.' : this.sessionError;
    }
    if ([429, 503].includes(error.status)) {
      const seconds = retryDelay(error);
      this.pausedUntil = Math.max(this.pausedUntil, Date.now() + seconds * 1000);
      entry.error += ` Повторите действие не раньше чем через ${seconds} сек.`;
      clearTimeout(this.pauseTimer);
      const wake = () => {
        this.changed();
        if (this.paused) this.pauseTimer = setTimeout(wake, Math.min(this.pausedUntil - Date.now() + 20, 2147483647));
      };
      this.pauseTimer = setTimeout(wake, Math.min(this.pausedUntil - Date.now() + 20, 2147483647));
    }
  }
  upload(file) {
    const problem = this.validate(file) || this.admissionError();
    if (problem) throw new Error(problem);
    const entry = this.entry(`upload-${++this.sequence}`, file.name);
    entry.file = file; entry.operation = 'checking';
    entry.done = (async () => {
      try { await this.adopt(entry, await this.api.upload(file)); entry.file = null; }
      catch (error) { await this.error(entry, error, true); }
      finally { entry.operation = null; this.changed(); }
    })();
    this.changed();
    return entry;
  }
  poll(entry) {
    clearTimeout(entry.timer);
    if (['checking', 'fixing'].includes(entry.data?.state)) entry.timer = setTimeout(() => this.refresh(entry, true), 1800);
  }
  async refresh(entry, polling = false) {
    if (!entry?.data?.document_id || entry.refreshing || entry.operation) return;
    clearTimeout(entry.timer); entry.refreshing = true;
    if (!polling) entry.error = '';
    this.changed();
    try {
      await this.adopt(entry, await this.api.get(entry.data.document_id));
      entry.error = ''; this.poll(entry);
    } catch (error) { await this.error(entry, error); }
    finally { entry.refreshing = false; this.changed(); }
  }
  async open(id) {
    const entry = [...this.documents.values()].find(item => item.data?.document_id === id) || this.entry(id, 'Открываем документ…');
    entry.data ||= { document_id: id, state: 'checking' };
    return entry;
  }
  async fix(entry) {
    if (!entry?.data?.report || isBusy(entry) || entry.refreshing || entry.data.current_version === 'corrected') return;
    const problem = this.admissionError();
    if (problem) { entry.error = problem; this.changed(); return; }
    entry.operation = 'fixing'; entry.error = ''; clearTimeout(entry.timer); this.changed();
    try {
      await this.adopt(entry, await this.api.get(entry.data.document_id));
      if (entry.data.current_version === 'corrected') return;
      if (['checking', 'fixing'].includes(entry.data.state)) { this.poll(entry); return; }
      await this.adopt(entry, await this.api.fix(entry.data.document_id));
    } catch (error) {
      await this.error(entry, error);
      if (entry.data && ![401, 403, 404].includes(error.status)) {
        try {
          await this.adopt(entry, await this.api.get(entry.data.document_id));
          if (entry.data.current_version === 'corrected') entry.error = '';
          this.poll(entry);
        } catch (recovery) { if ([401, 403, 404].includes(recovery.status)) await this.error(entry, recovery); }
      }
    } finally { entry.operation = null; this.changed(); }
  }
  dispose() {
    clearTimeout(this.pauseTimer);
    for (const entry of this.documents.values()) clearTimeout(entry.timer);
  }
}
