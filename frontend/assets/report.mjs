export const STATUSES = { error: "Ошибка", review: "Ручная проверка", recommendation: "Рекомендация" };
export const RULES = {
  F01: "Параметры страницы", F02: "Шрифты", F03: "Межстрочный интервал",
  F04: "Абзацный отступ", F05: "Нумерованные заголовки", F06: "Выравнивание заголовков и текста",
  F07: "Интервалы после заголовков", F08: "Содержание", F09: "Оформление содержания",
  F10: "Нумерация страниц", F12: "Ссылки на пункты", F15: "Индексы и единицы в формулах",
  F16: "Размеры знаков формул", F18: "Переносы формул", F19: "Нумерация формул",
  F20: "Ссылки на формулы", F22: "Рисунки", F23: "Нумерация таблиц", F24: "Оформление таблиц",
  F25: "Продолжение таблиц", F26: "Заголовки таблиц", F27: "Единицы и примечания в таблицах",
  F28: "Ячейки таблиц", F29: "Ссылки на приложения", F30: "Оформление приложений",
  F31: "Список источников", F32: "Ссылки на источники", F33: "Библиографическое описание",
  F34: "Допустимость источников", F35: "Сноски", F36: "Примечания",
};
export const PROPERTIES = {
  line_spacing: "Межстрочный интервал", first_line_indent: "Отступ первой строки",
  left_indent: "Отступ слева", right_indent: "Отступ справа", alignment: "Выравнивание",
  space_before: "Интервал перед абзацем", space_after: "Интервал после абзаца",
  font_name: "Шрифт", font_size: "Размер шрифта", bold: "Полужирное начертание",
  page_width: "Ширина страницы", page_height: "Высота страницы",
  heading_indentation: "Отступы заголовка", heading_gap: "Интервал после заголовка",
  text_alignment: "Выравнивание текста", unnumbered_heading_alignment: "Выравнивание заголовка",
  PAGE: "Поле номера страницы",
  top_margin: "Верхнее поле", bottom_margin: "Нижнее поле", left_margin: "Левое поле", right_margin: "Правое поле",
};

export function searchableText(finding) {
  return [finding.rule_id, RULES[finding.rule_id], finding.standard_reference, STATUSES[finding.status],
    finding.actual, finding.expected, ...finding.locations.flatMap(location =>
      [location.section, location.fragment_start, location.context, location.property, PROPERTIES[location.property]])]
    .filter(Boolean).join(" ").toLocaleLowerCase("ru");
}

export function filteredFindings(findings, { status, category, query, hideDone }, marks) {
  const words = query.trim().toLocaleLowerCase("ru").split(/\s+/).filter(Boolean);
  return findings.map((finding, index) => ({ finding, index })).filter(({ finding, index }) =>
    (status === "all" || finding.status === status) && (category === "all" || finding.rule_id === category)
    && (!hideDone || !marks.has(index)) && words.every(word => searchableText(finding).includes(word)));
}

// Indexes are scoped to an exact report digest, never carried across a recheck.
// The browser stores only the digest and checked indexes, not document excerpts.
export async function markStorageKey(document) {
  const scope = [document.document_id, document.current_version, document.checker_version,
    document.ruleset_version, document.report];
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(scope)));
  return "norma:marks:v1:" + Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

export function parseMarks(raw, count) {
  try {
    const items = JSON.parse(raw);
    return new Set(Array.isArray(items) ? items.filter(index => Number.isInteger(index) && index >= 0 && index < count) : []);
  } catch { return new Set(); }
}

export async function readResponse(response) {
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = null; }
  if (!response.ok) {
    const error = new Error(data?.error?.message || (typeof data?.detail === "string" && data.detail) || `Сервер вернул ошибку ${response.status}. Попробуйте позже.`);
    error.status = response.status;
    error.retryAfter = Number(response.headers.get("Retry-After")) || 5;
    throw error;
  }
  if (!data) throw new Error("Сервер не вернул ожидаемый ответ. Обновите статус документа.");
  return data;
}
