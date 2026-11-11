(function () {
  "use strict";

  const app = document.querySelector("[data-add-to-global-url]");
  const addButton = document.getElementById("add-to-global");
  const status = document.getElementById("global-deck-status");
  if (!app || !addButton || !status) {
    return;
  }

  const csrfInput = app.querySelector('input[name="csrfmiddlewaretoken"]');
  const csrfToken = csrfInput ? csrfInput.value : "";

  addButton.addEventListener("click", async function () {
    const selectedWord = document.querySelector(".transcript-word.is-selected");
    const translation = document.getElementById("translation-result");
    if (!selectedWord || !translation || !translation.textContent.trim()) {
      return;
    }

    addButton.disabled = true;
    status.textContent = "Adding…";
    try {
      const response = await fetch(app.dataset.addToGlobalUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({
          word: selectedWord.dataset.word,
          source_form: selectedWord.dataset.word,
          translation: translation.textContent.trim(),
          context: selectedWord.dataset.context || "",
          source_language: app.dataset.sourceLanguage || "de",
        }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || "Could not add this word.");
      }
      status.textContent = result.message;
      addButton.textContent = "In global deck";
    } catch (error) {
      status.textContent = error.message;
      addButton.disabled = false;
    }
  });
})();
