import { readonly, ref } from "vue";

export type Theme = "light" | "dark";

const STORAGE_KEY = "devpilot.theme";

function themeStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  if (typeof window.localStorage?.getItem === "function") return window.localStorage;
  return typeof window.sessionStorage?.getItem === "function" ? window.sessionStorage : null;
}

function initialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const saved = themeStorage()?.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return "light";
}

const theme = ref<Theme>(initialTheme());

function applyTheme(next: Theme): void {
  theme.value = next;
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = next;
    const paper = window.getComputedStyle(document.documentElement).getPropertyValue("--color-paper").trim();
    if (paper) document.querySelector('meta[name="theme-color"]')?.setAttribute("content", paper);
  }
}

applyTheme(theme.value);

export function useTheme() {
  applyTheme(theme.value);

  function setTheme(next: Theme): void {
    applyTheme(next);
    themeStorage()?.setItem(STORAGE_KEY, next);
  }

  function toggleTheme(): void {
    setTheme(theme.value === "light" ? "dark" : "light");
  }

  return { theme: readonly(theme), setTheme, toggleTheme };
}
