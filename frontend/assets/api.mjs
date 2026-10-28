import { readResponse } from './report.mjs';

export class DocumentAPI {
  session = null;
  async connect() {
    this.session = await this.request('/api/session');
    return this.session;
  }
  async request(path, init = {}) {
    const headers = new Headers(init.headers);
    if (init.method === 'POST') {
      if (!this.session) throw new Error('Подключитесь к серверу перед запуском проверки.');
      headers.set('X-CSRF-Token', this.session.csrf_token);
    }
    return readResponse(await fetch(path, { ...init, headers, credentials: 'same-origin' }));
  }
  upload(file) {
    const body = new FormData();
    body.append('file', file, file.name);
    return this.request('/api/documents', { method: 'POST', body });
  }
  path(id) { return `/api/documents/${encodeURIComponent(id)}`; }
  get(id) { return this.request(this.path(id)); }
  fix(id) { return this.request(`${this.path(id)}/fix`, { method: 'POST' }); }
  async download(data) {
    if (data.download_url !== `${this.path(data.document_id)}/download`) throw new Error('Ссылка на проверенную копию недоступна. Обновите статус.');
    const response = await fetch(data.download_url, { credentials: 'same-origin' });
    if (!response.ok) await readResponse(response);
    const encoded = (response.headers.get('Content-Disposition') || '').match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    let name = 'исправлено.docx';
    try { if (encoded) name = decodeURIComponent(encoded); } catch { /* Safe fallback. */ }
    return { name, blob: await response.blob() };
  }
}
