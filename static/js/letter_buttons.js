// Insert buttons for letters missing from an English keyboard (ñ). Loaded as a
// plain <script> in the browser and as a CommonJS module in Node tests.
//
// Markup: a [data-letter-row] holds one input and its
// <button data-insert-letter="ñ"> buttons.
(function (root) {
  "use strict";

  // The value with `text` replacing the selection start..end, and where the
  // caret goes: right after the inserted text.
  function insertAtCaret(value, start, end, text) {
    const length = value.length;
    const from = Math.min(Math.max(start, 0), length);
    const to = Math.min(Math.max(end, from), length);
    return {
      value: value.slice(0, from) + text + value.slice(to),
      caret: from + text.length,
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { insertAtCaret };
  }

  const doc = root.document;
  if (!doc) return;

  // Keep the caret in the input: a press on the button must not blur it.
  doc.addEventListener("mousedown", (event) => {
    if (event.target.closest("[data-insert-letter]")) event.preventDefault();
  });

  doc.addEventListener("click", (event) => {
    const button = event.target.closest("[data-insert-letter]");
    if (!button) return;
    const row = button.closest("[data-letter-row]");
    const input = row && row.querySelector("input");
    if (!input) return;
    const letter = button.dataset.insertLetter;
    const text = event.shiftKey ? letter.toUpperCase() : letter;
    const start = input.selectionStart === null ? input.value.length : input.selectionStart;
    const end = input.selectionEnd === null ? start : input.selectionEnd;
    const result = insertAtCaret(input.value, start, end, text);
    input.value = result.value;
    input.focus();
    input.setSelectionRange(result.caret, result.caret);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
})(typeof window !== "undefined" ? window : globalThis);
