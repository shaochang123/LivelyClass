(() => {
  const screens = [...document.querySelectorAll("#screens > .screen")];
  const navigation = document.querySelector(".primary-nav");
  const header = document.querySelector(".site-header");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const viewport = document.getElementById("screens");
  const wheelPause = 135;
  const wheelThreshold = 30;
  let active = 0;
  let running = [];
  let pendingStep = 0;
  let wheel = { direction: 0, lastAt: -Infinity };
  let touchStart = null;

  function indexFor(id) {
    return screens.findIndex(screen => screen.id === id);
  }

  function applyNavigation() {
    header.classList.toggle("home-header", active === 0);
    navigation.hidden = active === 0;
    for (const link of navigation.querySelectorAll("a")) {
      if (link.hash === `#${screens[active].id}`) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }

  function finishTransition() {
    for (const animation of running) animation.cancel();
    running = [];
    for (const [index, screen] of screens.entries()) {
      screen.hidden = index !== active;
      screen.inert = index !== active;
      screen.style.zIndex = "";
    }
  }

  function show(index, { record = true, animate = true, focus = true } = {}) {
    pendingStep = 0;
    if (index < 0 || index >= screens.length || index === active) return false;
    finishTransition();
    const previousIndex = active;
    const outgoing = screens[previousIndex];
    const incoming = screens[index];
    active = index;
    incoming.hidden = false;
    incoming.inert = false;
    if (record) history.pushState(null, "", `#${incoming.id}`);
    applyNavigation();
    if (focus) {
      const heading = incoming.querySelector("#job-section:not([hidden]) h2") ||
        document.getElementById(incoming.getAttribute("aria-labelledby"));
      heading?.focus({ preventScroll: true });
    }
    outgoing.inert = true;
    for (const media of outgoing.querySelectorAll("video, audio")) media.pause();
    window.LivelyCourses?.refresh(incoming.id);
    if (!animate) {
      finishTransition();
      return true;
    }

    const direction = index > previousIndex ? 1 : -1;
    outgoing.style.zIndex = "1";
    incoming.style.zIndex = "2";
    const transition = reducedMotion.matches ?
      { duration: 155, easing: "ease-out" } :
      { duration: 490, easing: "cubic-bezier(.24,.72,.24,1)" };
    const enter = reducedMotion.matches ?
      [{ opacity: 0 }, { opacity: 1 }] :
      [{ transform: `translateY(${direction * 100}%)` }, { transform: "translateY(0)" }];
    const leave = reducedMotion.matches ?
      [{ opacity: 1 }, { opacity: 1 }] :
      [{ transform: "translateY(0)", opacity: 1 }, { transform: `translateY(${direction * -48}%)`, opacity: .76 }];
    running = [outgoing.animate(leave, transition), incoming.animate(enter, transition)];
    const current = running;
    Promise.allSettled(current.map(animation => animation.finished)).then(() => {
      if (running !== current) return;
      finishTransition();
      const next = pendingStep;
      pendingStep = 0;
      if (next && !overlayOpen()) show(active + next);
    });
    return true;
  }

  function step(direction) {
    const index = active + direction;
    if (index < 0 || index >= screens.length) {
      pendingStep = 0;
      return false;
    }
    if (!running.length) return show(index);
    pendingStep = direction;
    return true;
  }

  function routeIndex() {
    const hash = location.hash.slice(1);
    const index = indexFor(hash);
    if (index >= 0) return index;
    if (["workflow", "course-style-options"].includes(hash)) return 1;
    const job = new URLSearchParams(location.search).get("job");
    return /^[0-9a-f]{32}$/.test(job || "") ? 1 : 0;
  }

  function overlayOpen() {
    return Boolean(document.querySelector("dialog[open]"));
  }

  function canScroll(target, direction) {
    if (target instanceof Element && target.closest(".screen")?.inert) return false;
    for (let area = target; area instanceof Element && area !== viewport; area = area.parentElement) {
      if (area.matches("[data-scroll-lock], select:focus")) return true;
      if (!area.matches("textarea, .screen-scroll")) continue;
      const remaining = direction > 0 ? area.scrollHeight - area.clientHeight - area.scrollTop : area.scrollTop;
      if (remaining > 2) return true;
    }
    return false;
  }

  window.addEventListener("wheel", event => {
    if (overlayOpen() || event.ctrlKey) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1;
    const pixels = Math.abs(event.deltaY * unit);
    if (!pixels || Math.abs(event.deltaX * unit) > pixels * 1.25) return;
    const direction = Math.sign(event.deltaY);
    const now = performance.now();

    // Wheel events do not expose trackpad phases. Treat a pause, a deliberate
    // reversal, or renewed input after a decaying tail as a fresh gesture.
    let fresh = now - wheel.lastAt > wheelPause;
    let distance = pixels;
    if (!fresh && wheel.direction !== direction) {
      wheel.reverse += pixels;
      fresh = !wheel.used || wheel.reverse >= wheelThreshold;
      if (!fresh) {
        wheel.lastAt = now;
        if (event.cancelable) event.preventDefault();
        return;
      }
      distance = wheel.reverse;
    } else if (!fresh) {
      fresh = wheel.used && wheel.tail >= 2 && pixels >= 13 && now - wheel.usedAt > 125;
    }
    if (fresh) wheel = { direction, distance: 0, reverse: 0, tail: 0, used: false };
    wheel.lastAt = now;
    wheel.reverse = 0;
    wheel.tail = pixels <= 5 ? wheel.tail + 1 : 0;

    if (!wheel.used) {
      if (canScroll(event.target, direction)) {
        wheel.distance = 0;
        return;
      }
      wheel.distance += distance;
      if (wheel.distance < wheelThreshold) return;
      wheel.distance = 0;
      if (!step(direction)) return;
      wheel.used = true;
      wheel.usedAt = now;
    }
    if (event.cancelable) event.preventDefault();
  }, { passive: false });

  viewport.addEventListener("touchstart", event => {
    touchStart = null;
    if (overlayOpen() || event.touches.length !== 1) return;
    const { clientX, clientY } = event.touches[0];
    touchStart = { clientX, clientY, down: canScroll(event.target, 1), up: canScroll(event.target, -1) };
  }, { passive: true });
  viewport.addEventListener("touchend", event => {
    const start = touchStart;
    touchStart = null;
    if (!start || overlayOpen()) return;
    const { clientX, clientY } = event.changedTouches[0];
    const change = start.clientY - clientY;
    if (Math.abs(change) < 55 || Math.abs(change) < Math.abs(clientX - start.clientX) * 1.2) return;
    if (change > 0 ? !start.down : !start.up) step(Math.sign(change));
  }, { passive: true });
  viewport.addEventListener("touchcancel", () => { touchStart = null; });

  document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.repeat || event.altKey || event.ctrlKey || event.metaKey || overlayOpen()) return;
    if (!(event.target instanceof Element) ||
        event.target.closest("input,textarea,select,button,video,[contenteditable],[data-scroll-lock]")) return;
    const direction = ({ ArrowDown: 1, PageDown: 1, ArrowUp: -1, PageUp: -1 })[event.key];
    if (!direction || canScroll(event.target, direction)) return;
    if (step(direction)) event.preventDefault();
  });

  document.addEventListener("click", event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    const link = event.target instanceof Element ? event.target.closest('a[href^="#"]') : null;
    if (!link) return;
    const index = indexFor(link.hash.slice(1));
    if (index < 0) return;
    event.preventDefault();
    if (!show(index)) document.getElementById(screens[index].getAttribute("aria-labelledby"))?.focus({ preventScroll: true });
  });

  function restoreRoute() {
    show(routeIndex(), { record: false });
  }
  window.addEventListener("hashchange", restoreRoute);
  window.addEventListener("popstate", restoreRoute);
  window.LivelyScreens = { go(id) { return show(indexFor(id)); } };
  active = routeIndex();
  finishTransition();
  applyNavigation();
})();
