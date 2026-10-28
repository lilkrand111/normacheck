import { STATUSES, RULES, PROPERTIES, filteredFindings } from './report.mjs';
import { isFixing } from './documents.mjs';

export const $ = id => document.getElementById(id);
export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
export function notice(id, text, error = false) {
  const node = $(id);
  node.textContent = text || ''; node.hidden = !text;
  node.classList.toggle('error-notice', error);
}
export function reportSelection(entry) {
  const findings = entry.data.report.findings;
  const matching = filteredFindings(findings, { ...entry.filters, hideDone: false }, entry.marks);
  const visible = entry.filters.hideDone ? matching.filter(item => !entry.marks.has(item.index)) : matching;
  if (!visible.some(item => item.index === entry.selectedFinding)) entry.selectedFinding = visible[0]?.index ?? null;
  return { findings, visible, matching, hiddenByMarks: matching.length > 0 && visible.length === 0 };
}
function checkbox(entry, index, onMark, label) {
  const input = element('input'); input.type = 'checkbox'; input.checked = entry.marks.has(index);
  input.disabled = isFixing(entry); input.dataset.focus = `mark-${label}-${index}`;
  input.setAttribute('aria-label', `Исправлено вручную: ${RULES[entry.data.report.findings[index].rule_id] || entry.data.report.findings[index].rule_id}, замечание ${index + 1}`);
  input.addEventListener('change', () => onMark(index, input.checked));
  return input;
}
function locationBlock(location) {
  const block = element('div', 'location');
  block.append(element('p', 'location-heading', [location.section || 'В документе', PROPERTIES[location.property] || location.property].filter(Boolean).join(' · ')));
  if (location.fragment_start) {
    block.append(element('p', 'fragment', location.fragment_start));
    const copy = element('button', 'copy', '⧉ Скопировать для поиска в Word'); copy.type = 'button';
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(location.fragment_start); copy.textContent = '✓ Скопировано · Скопировать снова'; $('announcement').textContent = 'Фрагмент скопирован'; }
      catch { copy.textContent = 'Выделите и скопируйте текст вручную'; $('announcement').textContent = 'Копирование недоступно. Выделите текст фрагмента вручную.'; }
    });
    block.append(copy);
  }
  if (location.context) block.append(element('p', 'context', location.context));
  if (!location.fragment_start) block.append(element('p', 'context', 'Для этого места текстовый фрагмент недоступен. Найдите указанный раздел или параметр в Word.'));
  return block;
}
function fillDetail(entry, finding, index, onMark) {
  const detail = $('finding-detail'); detail.replaceChildren();
  detail.append(element('p', `eyebrow status-${finding.status}`, `${finding.rule_id} / ${STATUSES[finding.status] || finding.status}`), element('h2', '', RULES[finding.rule_id] || 'Замечание по оформлению'));
  const comparison = element('div', 'comparison');
  for (const [label, value] of [['Обнаружено', finding.actual], ['Требуется', finding.expected]]) {
    const column = element('div'); column.append(element('small', '', label), element('p', '', value)); comparison.append(column);
  }
  detail.append(comparison, element('h3', '', `Места в документе · ${finding.locations.length}`));
  if (finding.locations.length) detail.append(locationBlock(finding.locations[0]));
  else detail.append(element('p', 'context', 'Замечание относится к документу в целом.'));
  if (finding.locations.length > 1) {
    const more = element('details', 'more-locations'); more.open = entry.expanded.has(index);
    const summary = element('summary', '', `Остальные места · ${finding.locations.length - 1}`);
    more.append(summary, ...finding.locations.slice(1).map(locationBlock));
    more.addEventListener('toggle', () => { if (more.open) entry.expanded.add(index); else entry.expanded.delete(index); });
    detail.append(more);
  }
  detail.append(element('p', 'reference', `Основание: ${finding.standard_reference}`));
  const mark = element('div', 'manual-mark'), label = element('label');
  label.append(checkbox(entry, index, onMark, 'detail'), element('span', '', 'Исправлено'));
  mark.append(label, element('p', '', isFixing(entry) ? 'Идёт повторная проверка. Ваши отметки предыдущего отчёта сохранены.' : 'Личная обратимая отметка. Не изменяет DOCX и не убирает замечание из результата проверки.'));
  detail.append(mark);
}
export function renderReport(entry, { onSelect, onMark, mobile }) {
  const { findings, visible, hiddenByMarks } = reportSelection(entry);
  $('count-all').textContent = findings.length;
  for (const status of Object.keys(STATUSES)) $('count-' + status).textContent = findings.filter(f => f.status === status).length;
  const rules = [...new Set(findings.map(f => f.rule_id))].sort();
  if (!rules.includes(entry.filters.category)) entry.filters.category = 'all';
  const options = [element('option', '', 'Категория: все категории'), ...rules.map(rule => element('option', '', `${rule} · ${RULES[rule] || 'Другое правило'}`))];
  options[0].value = 'all'; rules.forEach((rule, i) => { options[i + 1].value = rule; });
  $('category').replaceChildren(...options); $('category').value = entry.filters.category;
  $('status').value = entry.filters.status; $('search').value = entry.filters.query; $('hide-done').checked = entry.filters.hideDone;
  $('clear-search').hidden = !entry.filters.query;
  $('marked-count').textContent = `${entry.marks.size} из ${findings.length} отмечено вами`;
  $('visible-count').textContent = `Показано ${visible.length} из ${findings.length}`;
  $('list-heading').textContent = `${isFixing(entry) ? 'Предыдущий отчёт' : 'Замечания'} / ${visible.length}`;
  $('storage-warning').hidden = !entry.storageUnavailable;
  $('findings-column').hidden = !visible.length; $('details-slot').hidden = !visible.length;
  $('empty').hidden = Boolean(visible.length);
  if (!visible.length) {
    $('empty-kicker').textContent = !findings.length ? 'Проверка завершена' : hiddenByMarks ? 'Подходящие замечания скрыты' : `0 видимых · ${findings.length} найдено в документе`;
    $('empty-title').textContent = !findings.length ? 'В пределах выполненных проверок замечаний не найдено' : hiddenByMarks ? 'Все подходящие замечания отмечены и скрыты' : 'Ничего не найдено по условиям';
    $('empty-text').textContent = !findings.length ? 'Это результат выполненной проверки, а не гарантия полного соответствия СТП. Часть требований нужно проверить вручную.' : hiddenByMarks ? 'Снимите скрытие, чтобы снова увидеть отмеченные пункты. Ваши отметки и исходные результаты сохранятся.' : 'Поиск, категория и статус применены вместе. Измените условия или сбросьте их.';
    $('empty-action').hidden = !findings.length; $('empty-action').textContent = hiddenByMarks ? 'Показать отмеченные' : 'Сбросить поиск и фильтры';
    $('empty-action').dataset.action = hiddenByMarks ? 'show' : 'reset';
  }
  const signature = JSON.stringify([entry.key, entry.reportScope, entry.selectedFinding, entry.filters, [...entry.marks], isFixing(entry), mobile]);
  if ($('findings')._signature === signature) return;
  const focusKey = document.activeElement?.dataset?.focus;
  const scroll = $('findings').scrollTop;
  const detail = $('finding-detail');
  const detailSelection = `${entry.key}:${entry.selectedFinding}`;
  const detailScroll = detail._selection === detailSelection ? detail.scrollTop : 0;
  // Keep the single detail panel alive when moving between desktop and inline mobile layout.
  $('details-slot').append($('finding-detail'));
  $('findings').replaceChildren();
  for (const { finding, index } of visible) {
    const row = element('div', 'finding-row'); row.classList.toggle('active', index === entry.selectedFinding);
    const button = element('button', 'finding-select'); button.type = 'button'; button.dataset.focus = `select-${index}`;
    button.setAttribute('aria-current', index === entry.selectedFinding ? 'true' : 'false');
    button.setAttribute('aria-controls', 'finding-detail');
    if (mobile) button.setAttribute('aria-expanded', String(index === entry.selectedFinding));
    button.append(element('strong', '', RULES[finding.rule_id] || finding.rule_id), element('small', `status-${finding.status}`, `${finding.rule_id} · ${STATUSES[finding.status] || finding.status}`));
    button.addEventListener('click', () => onSelect(index));
    row.append(checkbox(entry, index, onMark, 'list'), button); $('findings').append(row);
    if (entry.selectedFinding === index) {
      fillDetail(entry, finding, index, onMark);
      if (mobile) row.append($('finding-detail'));
    }
  }
  if (!visible.length) $('finding-detail').replaceChildren();
  $('findings').scrollTop = scroll;
  detail.scrollTop = detailScroll;
  detail._selection = detailSelection;
  $('findings')._signature = signature;
  if (focusKey) {
    const target = [...document.querySelectorAll('[data-focus]')].find(node => node.dataset.focus === focusKey);
    if (target) target.focus({ preventScroll: true });
    else $('hide-done').focus({ preventScroll: true });
  }
}
export function renderCoverage(data) {
  $('warning-count').textContent = data.warnings.length ? `· ${data.warnings.length}` : '';
  $('show-coverage').textContent = data.warnings.length ? `Предупреждения · ${data.warnings.length} и ограничения ↗` : 'Ограничения проверки ↗';
  for (const [id, heading, values] of [['warnings', 'Особенности документа', data.warnings], ['limitations', 'Границы выполненных проверок', data.limitations.map(item => `${item.rule_id} — ${item.description}`)]]) {
    const container = $(id); container.replaceChildren();
    if (!values.length) continue;
    const list = element('ul'); list.append(...values.map(value => element('li', '', value)));
    container.append(element('h3', '', heading), list);
  }
}
