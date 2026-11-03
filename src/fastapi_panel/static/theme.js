// Loaded before the stylesheet so a dark-mode page never flashes white.
try {
  const saved = localStorage.getItem("fastapi-panel-theme");
  if (saved) document.documentElement.dataset.theme = saved;
} catch {}
