(function () {
  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if (event.key === "Enter" || event.code === "Space") {
      event.preventDefault();
      document.querySelector(".continue-button").click();
    }
  });
})();
