import { createRequire } from "node:module";
import assert from "node:assert/strict";
import test from "node:test";

const require = createRequire(import.meta.url);
const { insertAtCaret } = require("../letter_buttons.js");

test("insertAtCaret inserts at the caret in the middle of the text", () => {
  assert.deepEqual(insertAtCaret("espaol", 4, 4, "ñ"), { value: "español", caret: 5 });
});

test("insertAtCaret appends at the end and prepends at the start", () => {
  assert.deepEqual(insertAtCaret("ao", 2, 2, "ñ"), { value: "aoñ", caret: 3 });
  assert.deepEqual(insertAtCaret("ao", 0, 0, "ñ"), { value: "ñao", caret: 1 });
});

test("insertAtCaret replaces the selection and puts the caret after the letter", () => {
  assert.deepEqual(insertAtCaret("anno", 1, 3, "ñ"), { value: "año", caret: 2 });
  assert.deepEqual(insertAtCaret("abc", 0, 3, "Ñ"), { value: "Ñ", caret: 1 });
});

test("insertAtCaret works on an empty input", () => {
  assert.deepEqual(insertAtCaret("", 0, 0, "ñ"), { value: "ñ", caret: 1 });
});

test("insertAtCaret clamps a caret outside the text and a reversed selection", () => {
  assert.deepEqual(insertAtCaret("ab", 9, 12, "ñ"), { value: "abñ", caret: 3 });
  assert.deepEqual(insertAtCaret("ab", -3, -1, "ñ"), { value: "ñab", caret: 1 });
  assert.deepEqual(insertAtCaret("abcd", 3, 1, "ñ"), { value: "abcñd", caret: 4 });
});
