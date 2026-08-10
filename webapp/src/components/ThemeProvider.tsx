/**
 * Applies the viewer's resolved theme at runtime, and exposes the parts
 * of it that are not colours.
 *
 * The palette arrives from `/api/auth/me/theme` as CSS custom property
 * names mapped to hex colours, and is written with `style.setProperty` on
 * the document root — never into markup, and never through a `<style>`
 * tag built from a string. The backend has already restricted names to a
 * fixed allowlist and values to hex; this second check means a
 * compromised response still cannot inject anything.
 *
 * Changing a theme therefore needs no rebuild: the stylesheet compiled
 * into the app defines the same variables, and these override them.
 *
 * A failure is silent by design. If the request fails, or the payload is
 * malformed, the app keeps the palette from its own stylesheet and looks
 * exactly as it does today — a broken theme must never cost the user
 * their UI.
 *
 * **Nothing is applied unless a theme was actually configured.** These
 * are inline custom properties, which outrank every rule in the
 * stylesheet including `.dark`, so writing them unconditionally would
 * freeze one palette and disable light/dark for good. See the `scope`
 * check below.
 */
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "../lib/api";

/** Mirrors the server's allowlist. A name outside it is ignored. */
const ALLOWED_TOKENS = new Set([
  "--color-bg",
  "--color-surface",
  "--color-surface-hi",
  "--color-ink",
  "--color-ink-dim",
  "--color-marquee",
  "--color-marquee-dim",
  "--color-premiere",
  "--color-episode-watched",
  "--color-episode-unwatched",
  "--color-episode-check",
  "--color-success",
  "--color-warning",
  "--color-danger",
]);

/** #rgb, #rrggbb or #rrggbbaa — the same grammar the server enforces. */
const COLOR = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

/**
 * Shape name to radius. The radii live here and on the server, and the
 * client only ever *looks up* a name — a radius is never accepted from
 * the response, so a compromised payload cannot smuggle a CSS value in.
 */
export const CARD_RADII: Record<string, string> = {
  square: "0px",
  soft: "4px",
  rounded: "12px",
  "extra-rounded": "20px",
};

export const DECORATIONS = new Set([
  "none",
  "stars",
  "cinema",
  "anime",
  "horror",
  "abstract",
  "seasonal",
]);

export interface Appearance {
  cardShape: string;
  decoration: string;
}

const DEFAULT_APPEARANCE: Appearance = { cardShape: "rounded", decoration: "none" };

const AppearanceContext = createContext<Appearance>(DEFAULT_APPEARANCE);

/** Shape and decoration for the current viewer. Never user-identifying. */
export function useAppearance(): Appearance {
  return useContext(AppearanceContext);
}

export function applyTheme(tokens: Record<string, string>): number {
  const root = document.documentElement;
  let applied = 0;
  for (const [token, value] of Object.entries(tokens ?? {})) {
    if (!ALLOWED_TOKENS.has(token) || !COLOR.test(value)) continue;
    root.style.setProperty(token, value);
    applied += 1;
  }
  return applied;
}

/** Looks the radius up by name; an unknown shape falls back to the default. */
export function applyCardShape(shape: string): string {
  const radius = CARD_RADII[shape] ?? CARD_RADII[DEFAULT_APPEARANCE.cardShape];
  document.documentElement.style.setProperty("--radius-card", radius);
  return radius;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [appearance, setAppearance] = useState<Appearance>(DEFAULT_APPEARANCE);

  useEffect(() => {
    let cancelled = false;
    api
      .theme()
      .then((theme) => {
        if (cancelled) return;
        // `scope: null` means no rule matched — the backend is reporting
        // the built-in palette as a floor, not making a decision. Applying
        // it anyway is what broke light mode: `applyTheme` writes custom
        // properties as *inline* styles on <html>, and an inline
        // declaration outranks both `:root` and `.dark` in index.css. Since
        // DEFAULT_TOKENS is the dark palette, every light-mode viewer was
        // forced dark and the Telegram colour-scheme toggle stopped having
        // any effect at all.
        //
        // So a platform with nothing configured now looks exactly as
        // compiled, and light/dark works the way it did before themes
        // existed. Anything with a real scope — USER, BADGE, INTEREST,
        // SUBSCRIPTION or GLOBAL — is a deliberate override and still
        // applies, precedence untouched.
        if (theme.scope === null) return;
        applyTheme(theme.tokens);
        // Both are validated against local allowlists before use: an
        // unrecognised value is discarded rather than rendered.
        const cardShape = CARD_RADII[theme.card_shape] ? theme.card_shape : DEFAULT_APPEARANCE.cardShape;
        const decoration = DECORATIONS.has(theme.decoration)
          ? theme.decoration
          : DEFAULT_APPEARANCE.decoration;
        applyCardShape(cardShape);
        setAppearance({ cardShape, decoration });
      })
      .catch(() => {
        /* keep the compiled-in palette — see the module comment */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return <AppearanceContext.Provider value={appearance}>{children}</AppearanceContext.Provider>;
}
