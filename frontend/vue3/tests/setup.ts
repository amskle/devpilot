import { afterEach } from "vitest";
import { cleanup } from "@testing-library/vue";

// jsdom does not implement the browser's native dialog lifecycle.
HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  if (typeof window.localStorage?.clear === "function") window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});
