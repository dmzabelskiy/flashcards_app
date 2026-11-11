import { createRequire } from "node:module";
import assert from "node:assert/strict";
import test from "node:test";

const require = createRequire(import.meta.url);
const { pickVoice } = require("../tts.js");

const DUTCH_NL = { lang: "nl-NL", name: "Dutch (Netherlands)" };
const DUTCH_BE = { lang: "nl-BE", name: "Dutch (Belgium)" };
const GERMAN_DE = { lang: "de-DE", name: "German (Germany)" };

test("pickVoice picks an exact match over a same-base-language voice listed first", () => {
  const voice = pickVoice([GERMAN_DE, DUTCH_BE, DUTCH_NL], "nl-NL");
  assert.equal(voice, DUTCH_NL);
});

test("pickVoice falls back to the same base language", () => {
  const voice = pickVoice([GERMAN_DE, DUTCH_BE], "nl-NL");
  assert.equal(voice, DUTCH_BE);
});

test("pickVoice returns none when only a different language is available", () => {
  const voice = pickVoice([GERMAN_DE], "nl-NL");
  assert.equal(voice, null);
});

test("pickVoice matches case-insensitively and treats underscores as hyphens", () => {
  const voice = pickVoice([{ lang: "NL_nl" }], "nl-NL");
  assert.equal(voice.lang, "NL_nl");
});
