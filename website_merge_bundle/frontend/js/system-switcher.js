(() => {
  const links = [
    { href: "/", label: "\uD648" },
    { href: "/housing.html", label: "\uC9D1\uCd94\uCC9C" },
    { href: "/site-review.html", label: "\uBD80\uC9C0\uCd94\uCC9C" },
  ];

  const buildSwitcher = () => {
    const root = document.createElement("div");
    root.className = "system-switcher-fab";
    root.setAttribute("data-system-switcher", "");
    root.innerHTML = `
      <button
        type="button"
        class="system-switcher-toggle"
        data-system-switcher-toggle
        aria-label="\uC2DC\uC2A4\uD15C \uC804\uD658"
        aria-expanded="false"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 11.5L12 5l8 6.5"></path>
          <path d="M6.5 10.75V19h11V10.75"></path>
          <path d="M10.5 19v-5h3v5"></path>
        </svg>
      </button>
      <div class="system-switcher-menu" data-system-switcher-menu>
        ${links.map((link) => `<a href="${link.href}">${link.label}</a>`).join("")}
      </div>
    `;
    document.body.appendChild(root);
    return root;
  };

  const existing = document.querySelector("[data-system-switcher]");
  const root = existing || buildSwitcher();
  const toggle = root.querySelector("[data-system-switcher-toggle]");
  const menu = root.querySelector("[data-system-switcher-menu]");
  if (!toggle || !menu) return;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  let dragging = false;
  let pointerId = null;
  let startX = 0;
  let startY = 0;
  let originLeft = 14;
  let originTop = 14;
  let moved = false;

  const setPosition = (left, top) => {
    const rect = root.getBoundingClientRect();
    const maxLeft = window.innerWidth - rect.width - 8;
    const maxTop = window.innerHeight - rect.height - 8;
    const nextLeft = clamp(left, 8, Math.max(8, maxLeft));
    const nextTop = clamp(top, 8, Math.max(8, maxTop));
    root.style.left = `${nextLeft}px`;
    root.style.top = `${nextTop}px`;
    root.style.right = "auto";
    root.style.bottom = "auto";
  };

  const closeMenu = () => {
    root.classList.remove("is-open");
    toggle.setAttribute("aria-expanded", "false");
  };

  const openMenu = () => {
    root.classList.add("is-open");
    toggle.setAttribute("aria-expanded", "true");
  };

  const toggleMenu = () => {
    if (root.classList.contains("is-open")) {
      closeMenu();
    } else {
      openMenu();
    }
  };

  toggle.addEventListener("pointerdown", (event) => {
    pointerId = event.pointerId;
    moved = false;
    dragging = false;
    startX = event.clientX;
    startY = event.clientY;
    const rect = root.getBoundingClientRect();
    originLeft = rect.left;
    originTop = rect.top;
    toggle.setPointerCapture(pointerId);
  });

  toggle.addEventListener("pointermove", (event) => {
    if (pointerId !== event.pointerId) return;
    const deltaX = event.clientX - startX;
    const deltaY = event.clientY - startY;
    if (!dragging && Math.hypot(deltaX, deltaY) > 6) {
      dragging = true;
      moved = true;
      closeMenu();
      root.classList.add("is-dragging");
    }
    if (dragging) {
      setPosition(originLeft + deltaX, originTop + deltaY);
    }
  });

  const finishPointer = (event) => {
    if (pointerId !== event.pointerId) return;
    if (toggle.hasPointerCapture(pointerId)) {
      toggle.releasePointerCapture(pointerId);
    }
    pointerId = null;
    root.classList.remove("is-dragging");
    if (!moved) {
      toggleMenu();
    }
    dragging = false;
    moved = false;
  };

  toggle.addEventListener("pointerup", finishPointer);
  toggle.addEventListener("pointercancel", finishPointer);

  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) {
      closeMenu();
    }
  });

  window.addEventListener("resize", () => {
    const rect = root.getBoundingClientRect();
    setPosition(rect.left, rect.top);
  });
})();
