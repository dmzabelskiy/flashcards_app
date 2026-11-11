// Loaded as a plain <script> in the browser (attaches window.TtsCard) and as
// a CommonJS module in Node tests (sets module.exports).
(function (root) {
  "use strict";

  const AUTOPLAY_KEY = "tts-autoplay";
  const synth = root.speechSynthesis;

  function normaliseLang(lang) {
    return String(lang || "")
      .replace(/_/g, "-")
      .toLowerCase();
  }

  // Exact lang match first, then same base language (e.g. "nl-BE" for
  // "nl-NL"), else none: never fall back to a voice of a different language.
  function pickVoice(voices, lang) {
    const target = normaliseLang(lang);
    const targetBase = target.split("-")[0];
    const list = voices || [];
    const exact = list.find((v) => v && normaliseLang(v.lang) === target);
    if (exact) return exact;
    const sameBase = list.find((v) => v && normaliseLang(v.lang).split("-")[0] === targetBase);
    return sameBase || null;
  }

  function speak(text, lang) {
    if (!synth || !text) return;
    synth.cancel();
    const resolvedLang = lang || "de-DE";
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = resolvedLang;
    const voice = pickVoice(synth.getVoices(), resolvedLang);
    if (voice) utterance.voice = voice;
    synth.speak(utterance);
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      const buttons = document.querySelectorAll(".tts-button");
      if (!synth) {
        buttons.forEach((b) => (b.style.display = "none"));
        return;
      }
      buttons.forEach((button) => {
        button.addEventListener("click", function (event) {
          event.preventDefault();
          speak(button.getAttribute("data-tts-text"), button.getAttribute("data-tts-lang"));
        });
      });
      if (localStorage.getItem(AUTOPLAY_KEY) === "1" && buttons.length) {
        speak(buttons[0].getAttribute("data-tts-text"), buttons[0].getAttribute("data-tts-lang"));
      }
    });
  }

  const TtsCard = { speak: speak, pickVoice: pickVoice };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = TtsCard;
  } else {
    root.TtsCard = TtsCard;
  }
})(typeof window !== "undefined" ? window : globalThis);
