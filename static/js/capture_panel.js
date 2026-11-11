// Renders the translation panel's grammar summary for a captured word.
// Loaded as a plain <script> in the browser (attaches window.CapturePanel)
// and as a CommonJS module in Node tests (sets module.exports). Must not
// touch `document`.
(function (root) {
  "use strict";

  function describe(suggestion) {
    const metadata = suggestion || {};
    let status = "";
    if (metadata.trustworthy === false) {
      status = "Couldn't find the dictionary form of this word right now.";
    } else if (metadata.dictionary_status === "unavailable") {
      status = "Dictionary unavailable, using the offline form.";
    }
    return {
      grammar: metadata.part_of_speech || "—",
      gender: metadata.gender || "—",
      plural: metadata.plural || "—",
      example: metadata.example_sentence || "",
      status: status,
    };
  }

  function headline(result, text) {
    const word = result && result.word;
    return word || text;
  }

  const CapturePanel = { describe: describe, headline: headline };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = CapturePanel;
  } else {
    root.CapturePanel = CapturePanel;
  }
})(typeof window !== "undefined" ? window : globalThis);
