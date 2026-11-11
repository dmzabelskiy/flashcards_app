import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { describe, headline } = require("../capture_panel.js");

test("describes a dictionary noun", () => {
  assert.deepEqual(
    describe({
      part_of_speech: "noun",
      gender: "neuter",
      plural: "Häuser",
      example_sentence: "Das Haus steht seit 1898.",
      dictionary_status: "found",
      trustworthy: true,
    }),
    {
      grammar: "noun",
      gender: "neuter",
      plural: "Häuser",
      example: "Das Haus steht seit 1898.",
      status: "",
    },
  );
});

test("reports an unavailable dictionary", () => {
  const panel = describe({ part_of_speech: "verb", dictionary_status: "unavailable", trustworthy: true });
  assert.equal(panel.status, "Dictionary unavailable, using the offline form.");
  assert.equal(panel.example, "");
  assert.equal(panel.plural, "—");
});

test("warns when the word cannot be saved", () => {
  const panel = describe({ dictionary_status: "unavailable", trustworthy: false });
  assert.equal(panel.status, "Couldn't find the dictionary form of this word right now.");
});

test("tolerates a missing suggestion", () => {
  assert.deepEqual(describe(undefined), {
    grammar: "—", gender: "—", plural: "—", example: "", status: "",
  });
});

test("headline shows the resolved noun front with its article", () => {
  assert.equal(headline({ word: "das Haus" }, "Häusern"), "das Haus");
});

test("headline shows the resolved verb infinitive", () => {
  assert.equal(headline({ word: "aufstehen" }, "steht"), "aufstehen");
});

test("headline falls back to the clicked text when word is missing or empty", () => {
  assert.equal(headline({}, "steht"), "steht");
  assert.equal(headline({ word: "" }, "steht"), "steht");
  assert.equal(headline(undefined, "steht"), "steht");
});
