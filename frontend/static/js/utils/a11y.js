/**
 * Accessibility & Keyboard Management.
 * Manages focus traps, keyboard shortcuts, and screen-reader live announcements.
 */

let activeTrapCleanup = null;
let lastFocusedElement = null;

export function setTriggerElement(element) {
  if (element && typeof element.focus === "function") {
    lastFocusedElement = element;
  }
}

/**
 * Announces a message to screen readers via an aria-live region.
 */
export function announceLive(message, politeness = "polite") {
  let liveRegion = document.getElementById("a11y-live-region");
  if (!liveRegion) {
    liveRegion = document.createElement("div");
    liveRegion.id = "a11y-live-region";
    liveRegion.setAttribute("aria-live", politeness);
    liveRegion.setAttribute("aria-atomic", "true");
    liveRegion.className = "sr-only";
    document.body.appendChild(liveRegion);
  }
  // Clear and update to trigger announcement
  liveRegion.textContent = "";
  setTimeout(() => {
    liveRegion.textContent = message;
  }, 50);
}

/**
 * Activates a focus trap inside the given container element.
 * Captures Tab / Shift+Tab to keep focus within container.
 * Remembers previously focused element for later restoration.
 */
export function trapFocus(container, initialFocusSelector = null, triggerElement = null) {
  if (activeTrapCleanup) {
    activeTrapCleanup();
  }

  if (triggerElement) {
    lastFocusedElement = triggerElement;
  } else if (!lastFocusedElement) {
    lastFocusedElement = document.activeElement;
  }

  const getFocusable = () => {
    return Array.from(
      container.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
    ).filter(el => !el.hasAttribute("disabled") && el.offsetParent !== null);
  };

  const handleKeyDown = (e) => {
    if (e.key !== "Tab") return;

    const focusable = getFocusable();
    if (focusable.length === 0) {
      e.preventDefault();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.shiftKey) {
      if (document.activeElement === first || !container.contains(document.activeElement)) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last || !container.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  document.addEventListener("keydown", handleKeyDown);

  // Focus initial element
  const focusTarget = initialFocusSelector
    ? container.querySelector(initialFocusSelector)
    : getFocusable()[0];

  if (focusTarget) {
    setTimeout(() => focusTarget.focus(), 50);
  } else {
    container.setAttribute("tabindex", "-1");
    setTimeout(() => container.focus(), 50);
  }

  activeTrapCleanup = () => {
    document.removeEventListener("keydown", handleKeyDown);
    activeTrapCleanup = null;
  };

  return activeTrapCleanup;
}

/**
 * Releases active focus trap and restores focus to triggering element.
 */
export function releaseFocusTrap() {
  if (activeTrapCleanup) {
    activeTrapCleanup();
  }
  if (lastFocusedElement) {
    const el = lastFocusedElement;
    lastFocusedElement = null;
    const restore = () => {
      if (el && typeof el.focus === "function") {
        el.focus();
      }
    };
    restore();
    setTimeout(restore, 50);
  }
}

/**
 * Sets up global keyboard shortcuts:
 * - '/' focuses the search bar (unless already typing in an input/textarea)
 * - 'Escape' closes modal/drawer
 */
export function setupGlobalKeybindings({ onSlash, onEscape }) {
  document.addEventListener("keydown", (e) => {
    // Escape key
    if (e.key === "Escape") {
      e.preventDefault();
      if (typeof onEscape === "function") {
        onEscape(e);
      }
      return;
    }

    // '/' key to focus search
    if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const active = document.activeElement;
      const isInput =
        active &&
        (active.tagName === "INPUT" ||
          active.tagName === "TEXTAREA" ||
          active.isContentEditable);
      if (!isInput) {
        e.preventDefault();
        if (typeof onSlash === "function") {
          onSlash();
        }
      }
    }
  });
}
