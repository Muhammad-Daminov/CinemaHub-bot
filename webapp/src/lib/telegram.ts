/** Thin wrapper around window.Telegram.WebApp — the only place that touches the global. */

interface TelegramWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  ready: () => void;
  expand: () => void;
  onEvent: (event: string, handler: () => void) => void;
  themeParams: Record<string, string>;
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

export const isInsideTelegram = Boolean(webApp);
