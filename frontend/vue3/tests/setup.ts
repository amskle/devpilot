import { afterEach } from "vitest";
import { cleanup } from "@testing-library/vue";

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  if (typeof window.localStorage?.clear === "function") window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});
