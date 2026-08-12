/** Thin wrapper around window.Telegram.WebApp — the only place that touches the global. */

interface TelegramWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  ready: () => void;
  expand: () => void;
  onEvent: (event: string, handler: () => void) => void;
  themeParams: Record<string, string>;
  /** Opens a t.me link inside Telegram itself rather than a browser tab. */
  openTelegramLink?: (url: string) => void;
  openLink?: (url: string) => void;
}

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

const webApp = window.Telegram?.WebApp;

export function initTelegramApp(): void {
  webApp?.ready();
  webApp?.expand();
}

export function getInitData(): string {
  return webApp?.initData ?? "";
}

export function getColorScheme(): "light" | "dark" {
  return webApp?.colorScheme ?? "dark";
}

export function onThemeChange(callback: () => void): void {
  webApp?.onEvent("themeChanged", callback);
}

/**
 * Opens an external link the way the host expects.
 *
 * A t.me address goes through `openTelegramLink`, which hands it to the
 * Telegram client — opening it in the in-app browser instead would show a
 * join page the user cannot act on without leaving the app again.
 * Everything else, and every non-Telegram host, falls back to a normal
 * window open so the link still works outside the Mini App.
 */
export function openLink(url: string): void {
  const telegramLink = /^https:\/\/t\.me\//i.test(url);
  if (telegramLink && webApp?.openTelegramLink) {
    webApp.openTelegramLink(url);
    return;
  }
  if (webApp?.openLink) {
    webApp.openLink(url);
    return;
  }
  window.open(url, "_blank", "noopener");
}

export const isInsideTelegram = Boolean(webApp);
