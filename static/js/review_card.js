(function () {
  const started = Date.now();
  const elapsedInput = document.getElementById("elapsed-seconds");
  function setElapsed() {
    if (elapsedInput) elapsedInput.value = Math.max(0, Math.round((Date.now() - started) / 1000));
  }
  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if (event.code === "Space") {
      const link = document.querySelector(".show-answer");
      if (link) {
        event.preventDefault();
        window.location = link.href;
      }
    }
    const button = document.querySelector(`[data-key="${event.key}"]`);
    if (button) {
      event.preventDefault();
      setElapsed();
      button.click();
    }
  });
  const form = document.getElementById("rating-form");
  if (form) form.addEventListener("submit", setElapsed);
})();
