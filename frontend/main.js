/**
 * Configuration & Production Environment Endpoints
 * Automatically points to localhost during local preview, or cloud endpoints in production.
 */
const ENV = {
  RENDER_BACKEND_URL: window.__EMA_BACKEND_URL__ ||
    (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
      ? "http://127.0.0.1:8000"
      : window.location.origin),
  NETLIFY_FRONTEND_URL: window.location.origin
};

document.addEventListener("DOMContentLoaded", () => {
  // Production uses the Netlify same-origin proxy.
  const signInBtn = document.getElementById("btn-sign-in");
  const mobileSignInBtn = document.getElementById("mobile-sign-in");
  const targetLoginUrl = `${ENV.RENDER_BACKEND_URL}/auth/login?browser=1`;

  if (signInBtn) signInBtn.setAttribute("href", targetLoginUrl);
  if (mobileSignInBtn) mobileSignInBtn.setAttribute("href", targetLoginUrl);

  initHardwareResourceGuard();
  initMobileMenu();
  initStatsObserver();
});

/**
 * 1. Low Resource & Energy Guard
 * Pauses background video decoding when tab is minimized or hidden.
 */
function initHardwareResourceGuard() {
  const video = document.querySelector(".bg-video");
  if (!video) return;

  const tryPlay = () => {
    if (document.hidden) return;
    video.play().catch(() => {
      // Autoplay can be blocked by the browser; the page remains usable.
    });
  };

  video.addEventListener("error", () => {
    video.classList.add("video-unavailable");
  }, { once: true });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) video.pause();
    else tryPlay();
  });

  tryPlay();
}

/**
 * 2. Mobile Drawer & Navigation Management
 */
function initMobileMenu() {
  const burgerBtn = document.getElementById("burger-btn");
  const overlay = document.getElementById("mobile-overlay");
  const sheet = document.getElementById("mobile-sheet");
  const links = document.querySelectorAll(".mobile-link");

  if (!burgerBtn || !overlay || !sheet) return;

  function toggleMenu(open) {
    const shouldOpen = open !== undefined ? open : overlay.hasAttribute("hidden");

    if (shouldOpen) {
      overlay.removeAttribute("hidden");
      sheet.removeAttribute("hidden");
      document.body.classList.add("menu-open");
      burgerBtn.setAttribute("aria-expanded", "true");
    } else {
      overlay.setAttribute("hidden", "");
      sheet.setAttribute("hidden", "");
      document.body.classList.remove("menu-open");
      burgerBtn.setAttribute("aria-expanded", "false");
    }
  }

  burgerBtn.addEventListener("click", () => toggleMenu());
  overlay.addEventListener("click", () => toggleMenu(false));

  links.forEach(link => {
    link.addEventListener("click", () => {
      links.forEach(l => l.classList.remove("active"));
      link.classList.add("active");
      toggleMenu(false);
    });
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !overlay.hasAttribute("hidden")) {
      toggleMenu(false);
    }
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 720 && !overlay.hasAttribute("hidden")) {
      toggleMenu(false);
    }
  });
}

/**
 * 3. High-Performance Stat Counter Animation
 * Runs once via IntersectionObserver using requestAnimationFrame with easeOutCubic interpolation.
 */
function initStatsObserver() {
  const statItems = document.querySelectorAll(".stat-item");
  if (!statItems.length) return;

  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

  const startCounter = (el, i) => {
    const target = parseFloat(el.getAttribute("data-target")) || 0;
    const decimals = parseInt(el.getAttribute("data-decimals"), 10) || 0;
    const suffix = el.getAttribute("data-suffix") || "";
    const valueEl = el.querySelector(".stat-val");

    if (!valueEl) return;

    const duration = 1500 + i * 80;
    const delay = 480 + i * 90;

    setTimeout(() => {
      const startTime = performance.now();

      function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const currentVal = target * easeOutCubic(progress);

        valueEl.textContent = `${currentVal.toFixed(decimals)}${suffix}`;

        if (progress < 1) {
          requestAnimationFrame(update);
        } else {
          valueEl.textContent = `${target.toFixed(decimals)}${suffix}`;
        }
      }

      requestAnimationFrame(update);
    }, delay);
  };

  const observer = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          statItems.forEach((el, index) => startCounter(el, index));
          obs.disconnect(); // Execute once and unbind to eliminate scroll overhead
        }
      });
    },
    { threshold: 0.25 }
  );

  const footer = document.querySelector(".stats-footer");
  if (footer) observer.observe(footer);
}