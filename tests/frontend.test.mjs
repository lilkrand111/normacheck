import test from "node:test";
import assert from "node:assert/strict";
import { filteredFindings, markStorageKey, parseMarks, readResponse, retryDelay, operationErrorMessage } from "../frontend/assets/report.mjs";

const findings = [
  { rule_id: "F03", status: "error", actual: "Интервал — 1,5", expected: "Интервал 1,0", standard_reference: "Уточнение заказчика", locations: [{ section: "Введение", fragment_start: "Система\tпроверяет\nдокумент", property: "line_spacing" }] },
  { rule_id: "F22", status: "review", actual: "Проверьте рисунок", expected: "Проверьте подпись", standard_reference: "2.5.5", locations: [{ section: "", context: "Рисунок 1", property: "unknown_property" }] },
  { rule_id: "F02", status: "recommendation", actual: "Размер заголовка", expected: "Уточните размер", standard_reference: "2.1.1", locations: [] },
];
const filters = { status: "all", category: "all", query: "", hideDone: false };

test("search combines category/status/manual filters and searches all location text", () => {
  assert.equal(filteredFindings(findings, { ...filters, query: "СИСТЕМА документ" }, new Set()).length, 1);
  assert.equal(filteredFindings(findings, { ...filters, query: "unknown_property" }, new Set())[0].index, 1);
  assert.equal(filteredFindings(findings, { ...filters, status: "error", category: "F22" }, new Set()).length, 0);
  assert.equal(filteredFindings(findings, { ...filters, status: "review", category: "F22" }, new Set()).length, 1);
  assert.deepEqual(filteredFindings(findings, { ...filters, hideDone: true }, new Set([0])).map(item => item.index), [1, 2]);
  assert.equal(findings[0].locations[0].fragment_start, "Система\tпроверяет\nдокумент");
});

test("manual marks cannot transfer to another document, version, ruleset, or reordered report", async () => {
  const doc = { document_id: "one", current_version: "original", checker_version: "1", ruleset_version: "1", report: { findings } };
  const key = await markStorageKey(doc);
  assert.equal(key, await markStorageKey(structuredClone(doc)));
  for (const change of [
    { document_id: "two" }, { current_version: "corrected" }, { ruleset_version: "2" },
    { checker_version: "2" }, { report: { findings: [...findings].reverse() } },
    { report: { findings: [findings[0], findings[0]] } },
  ]) assert.notEqual(key, await markStorageKey({ ...doc, ...change }));
  assert.equal(key.includes("Система"), false);
  assert.deepEqual([...parseMarks('[0,0,1,-1,99,"2",null]', 3)], [0, 1]);
  assert.equal(parseMarks("invalid", 3).size, 0);
});

test("error responses survive middleware text, detail format, rate limits and auth failures", async () => {
  for (const status of [401, 403, 408, 409, 413, 422, 429, 500, 503, 504, 507]) {
    await assert.rejects(readResponse(new Response(JSON.stringify({ error: { message: "Понятная ошибка" } }), { status, headers: { "Retry-After": "7" } })), error => error.status === status && error.message === "Понятная ошибка" && error.retryAfter === 7);
  }
  await assert.rejects(readResponse(new Response("Invalid host header", { status: 400 })), error => error.status === 400);
  await assert.rejects(readResponse(new Response('{"detail":"Слишком большой файл"}', { status: 413 })), /Слишком большой файл/);
  await assert.rejects(readResponse(new Response("not JSON")), /ожидаемый ответ/);
  assert.deepEqual(await readResponse(new Response('{"report":null}')), { report: null });
});

test("mutation errors distinguish rate limits, upload and processing timeouts", () => {
  assert.match(operationErrorMessage({ status: 429 }), /Слишком много запросов/);
  assert.match(operationErrorMessage({ status: 408 }), /загрузить файл/);
  assert.match(operationErrorMessage({ status: 504 }), /Проверка или исправление/);
  assert.equal(operationErrorMessage({ status: 503, message: "Все обработчики заняты" }), "Все обработчики заняты");
  assert.equal(retryDelay({ status: 429, retryAfter: 3600 }), 3600);
  assert.equal(retryDelay({ status: 503, retryAfter: 2.2 }), 3);
  for (const retryAfter of [undefined, NaN, Infinity, -1, 0]) {
    assert.equal(retryDelay({ status: 429, retryAfter }), 5);
  }
  assert.equal(retryDelay({ status: 408, retryAfter: 7 }), 0);
});
