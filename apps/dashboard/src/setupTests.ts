import "@testing-library/jest-dom/vitest";

// Node 26 ships its own (gated) localStorage that shadows JSDOM's via vitest's
// populateGlobal. Re-bind the real JSDOM storage onto the global window so
// `window.localStorage` works the same way it would in a real browser.
const jsdomWindow = (globalThis as unknown as { jsdom?: { window: Window } })
  .jsdom?.window;
if (jsdomWindow) {
  for (const name of ["localStorage", "sessionStorage"] as const) {
    if (typeof window[name] === "undefined" && typeof jsdomWindow[name] !== "undefined") {
      Object.defineProperty(window, name, {
        configurable: true,
        get: () => jsdomWindow[name],
      });
    }
  }
}

// jsdom does not implement scrollIntoView — stub it so chat auto-scroll is a
// no-op in tests rather than throwing.
if (!("scrollIntoView" in Element.prototype)) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
