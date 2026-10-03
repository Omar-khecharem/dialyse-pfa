(() => {
  const sidebar = document.getElementById("sidebar");
  const overlay = document.getElementById("sidebarOverlay");
  const btn = document.querySelector("[data-sidebar-toggle]");

  if (!sidebar || !overlay || !btn) return;

  const open = () => {
    sidebar.classList.remove("-translate-x-full");
    sidebar.classList.add("translate-x-0");
    overlay.classList.remove("hidden");
  };

  const close = () => {
    sidebar.classList.add("-translate-x-full");
    sidebar.classList.remove("translate-x-0");
    overlay.classList.add("hidden");
  };

  btn.addEventListener("click", () => {
    const isClosed = sidebar.classList.contains("-translate-x-full");
    isClosed ? open() : close();
  });

  overlay.addEventListener("click", close);
})();
