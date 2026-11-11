(function () {
  var shell = document.querySelector(".app-shell");
  var toggle = document.getElementById("sidebarToggle");
  if (!shell || !toggle) return;

  var KEY = "sidebar-collapsed";
  var stored = localStorage.getItem(KEY); // "true" | "false" | null
  var mobile = window.matchMedia("(max-width: 860px)");

  // Desktop: the toggle collapses the sidebar to an icon rail (persisted).
  function applyCollapsed(collapsed) {
    shell.dataset.collapsed = collapsed ? "true" : "false";
    if (!mobile.matches) {
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
  }

  // Auto-by-width default when the user has not made an explicit choice.
  var collapsed = stored === null ? window.matchMedia("(max-width: 999px)").matches : stored === "true";
  applyCollapsed(collapsed);

  function closeDrawer() {
    shell.classList.remove("drawer-open");
    if (mobile.matches) toggle.setAttribute("aria-expanded", "false");
  }
  // Mobile: the drawer starts closed regardless of the desktop collapse state.
  if (mobile.matches) toggle.setAttribute("aria-expanded", "false");

  toggle.addEventListener("click", function () {
    if (mobile.matches) {
      // Mobile: the toggle opens/closes the off-canvas drawer.
      var open = !shell.classList.contains("drawer-open");
      shell.classList.toggle("drawer-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    } else {
      collapsed = shell.dataset.collapsed !== "true";
      applyCollapsed(collapsed);
      localStorage.setItem(KEY, collapsed ? "true" : "false");
    }
  });

  // On mobile, tapping the backdrop or a nav link closes the drawer.
  shell.addEventListener("click", function (event) {
    if (!mobile.matches) return;
    if (event.target.closest(".sidebar-backdrop") || event.target.closest(".side-item")) {
      closeDrawer();
    }
  });

  // Reset drawer/aria state when crossing the breakpoint.
  mobile.addEventListener("change", function () {
    closeDrawer();
    applyCollapsed(shell.dataset.collapsed === "true");
  });
})();
