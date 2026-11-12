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

// ---- Handler wiring, on a minimal fake document ----

const { install } = require("../letter_buttons.js");

class FakeElement {
  constructor(tag, attrs = {}, children = []) {
    this.tag = tag;
    this.attrs = attrs;
    this.parent = null;
    this.children = children;
    children.forEach((child) => {
      child.parent = this;
    });
  }

  get dataset() {
    const data = {};
    for (const [name, value] of Object.entries(this.attrs)) {
      if (name.startsWith("data-")) {
        const key = name.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase());
        data[key] = value;
      }
    }
    return data;
  }

  matches(selector) {
    const attr = selector.match(/^\[([\w-]+)\]$/);
    return attr ? attr[1] in this.attrs : selector === this.tag;
  }

  closest(selector) {
    for (let node = this; node; node = node.parent) {
      if (node.matches(selector)) return node;
    }
    return null;
  }

  querySelector(selector) {
    for (const child of this.children) {
      if (child.matches(selector)) return child;
      const found = child.querySelector(selector);
      if (found) return found;
    }
    return null;
  }
}

class FakeInput extends FakeElement {
  constructor(value = "", selectionStart = value.length, selectionEnd = selectionStart) {
    super("input");
    this.value = value;
    this.selectionStart = selectionStart;
    this.selectionEnd = selectionEnd;
    this.focused = false;
    this.events = [];
  }

  focus() {
    this.focused = true;
  }

  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }

  dispatchEvent(event) {
    this.events.push(event);
    return true;
  }
}

function letterButton(letter = "ñ") {
  return new FakeElement("button", { type: "button", "data-insert-letter": letter });
}

// A page with one form per row; `submitted` counts form submissions.
function page(...inputs) {
  const submitted = { count: 0 };
  const rows = inputs.map((input) => {
    const button = letterButton();
    const row = new FakeElement("div", { "data-letter-row": "" }, [input, button]);
    const form = new FakeElement("form", {}, [row]);
    form.submit = form.requestSubmit = () => {
      submitted.count += 1;
    };
    return { form, row, button };
  });
  const listeners = {};
  const doc = {
    addEventListener(type, handler) {
      (listeners[type] = listeners[type] || []).push(handler);
    },
  };
  install(doc);
  // Fire `type` at `target` the way the browser delivers it to a document listener.
  function fire(type, target, props = {}) {
    const event = {
      type,
      target,
      shiftKey: false,
      defaultPrevented: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
      ...props,
    };
    (listeners[type] || []).forEach((handler) => handler(event));
    return event;
  }
  return { rows, listeners, fire, submitted };
}

test("install registers one mousedown and one click listener on the document", () => {
  const { listeners } = page(new FakeInput());

  assert.deepEqual(Object.keys(listeners).sort(), ["click", "mousedown"]);
  assert.equal(listeners.mousedown.length, 1);
  assert.equal(listeners.click.length, 1);
});

test("a click inserts the letter into the input of its own row only", () => {
  const first = new FakeInput("ao");
  const second = new FakeInput("ao");
  const { rows, fire } = page(first, second);
  first.setSelectionRange(1, 1);
  second.setSelectionRange(1, 1);

  fire("click", rows[1].button);

  assert.equal(first.value, "ao");
  assert.equal(second.value, "año");
  assert.equal(first.events.length, 0);
});

test("a click replaces the selection, focuses the input and puts the caret after the letter", () => {
  const input = new FakeInput("anno", 1, 3);
  const { rows, fire } = page(input);

  fire("click", rows[0].button);

  assert.equal(input.value, "año");
  assert.equal(input.focused, true);
  assert.equal(input.selectionStart, 2);
  assert.equal(input.selectionEnd, 2);
});

test("a click on an input without a selection appends at the end", () => {
  const input = new FakeInput("espa");
  input.selectionStart = input.selectionEnd = null;
  const { rows, fire } = page(input);

  fire("click", rows[0].button);

  assert.equal(input.value, "españ");
  assert.equal(input.selectionStart, 5);
});

test("a click dispatches exactly one bubbling input event", () => {
  const input = new FakeInput("a");
  const { rows, fire } = page(input);

  fire("click", rows[0].button);

  assert.equal(input.events.length, 1);
  assert.equal(input.events[0].type, "input");
  assert.equal(input.events[0].bubbles, true);
});

test("Shift inserts the capital letter", () => {
  const input = new FakeInput("a", 1);
  const { rows, fire } = page(input);

  fire("click", rows[0].button, { shiftKey: true });

  assert.equal(input.value, "aÑ");
  assert.equal(input.selectionStart, 2);
});

test("the mouse press on a letter button is cancelled so the input keeps focus", () => {
  const { rows, fire } = page(new FakeInput());

  assert.equal(fire("mousedown", rows[0].button).defaultPrevented, true);
});

test("a press elsewhere, even on the input, is left alone", () => {
  const input = new FakeInput();
  const { rows, fire } = page(input);

  assert.equal(fire("mousedown", input).defaultPrevented, false);
  assert.equal(fire("mousedown", rows[0].row).defaultPrevented, false);
});

test("a click on a letter button submits no form and cancels nothing", () => {
  const { rows, fire, submitted } = page(new FakeInput());

  const event = fire("click", rows[0].button);

  assert.equal(submitted.count, 0);
  assert.equal(event.defaultPrevented, false);
  assert.equal(rows[0].button.attrs.type, "button");
});

test("a click outside a letter button changes nothing", () => {
  const input = new FakeInput("ao");
  const { rows, fire, submitted } = page(input);

  fire("click", input);
  fire("click", rows[0].row);

  assert.equal(input.value, "ao");
  assert.equal(input.events.length, 0);
  assert.equal(input.focused, false);
  assert.equal(submitted.count, 0);
});

test("a letter button outside a letter row does nothing", () => {
  const stray = letterButton();
  new FakeElement("div", {}, [stray]);
  const { fire } = page(new FakeInput());

  assert.doesNotThrow(() => fire("click", stray));
});
