document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;

  const activeElement = document.activeElement;
  const isTyping =
    activeElement &&
    (activeElement.tagName === "INPUT" ||
      activeElement.tagName === "TEXTAREA" ||
      activeElement.tagName === "SELECT");
  if (isTyping) return;

  const choice = document.querySelector(`[data-drill-key="${event.key}"]`);
  if (!choice) return;

  event.preventDefault();
  choice.click();
});
