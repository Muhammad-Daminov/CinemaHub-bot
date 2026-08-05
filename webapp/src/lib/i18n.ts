/**
 * Frontend half of the bot's translation system — not a second one.
 *
 * The catalogs live in app/locales/*.json and are served by
 * GET /api/i18n/{lang}; nothing here holds a string of its own. That is
 * the whole point: a wording change in the JSON reaches the bot and the
 * Mini App at once, instead of being made twice and forgotten once.
 *
 * The backend already merges the requested language over Uzbek, so a key
 * present in any locale resolves. A key present in none returns the key
 * itself — a visible "app.row_top" in the UI is a bug report, whereas a
 * blank label or a thrown error is a mystery.
 */
import { createContext, useContext } from "react";

export type Language = "uz" | "ru" | "en";

export const LANGUAGES: { value: Language; labelKey: string }[] = [
  { value: "uz", labelKey: "common.lang_uz" },
  { value: "ru", labelKey: "common.lang_ru" },
  { value: "en", labelKey: "common.lang_en" },
];

export type Translator = (key: string, params?: Record<string, string | number>) => string;

/** Mirrors app.core.i18n.t: {placeholder} substitution, never throws. */
export function translate(
  catalog: Record<string, string>,
  key: string,
  params?: Record<string, string | number>,
): string {
  const template = catalog[key];
  if (template === undefined) return key;
  if (!params) return template;

  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

export async function fetchTranslations(lang: Language): Promise<Record<string, string>> {
  const response = await fetch(`/api/i18n/${lang}`);
  if (!response.ok) throw new Error(`i18n ${lang}: ${response.status}`);
  const body = (await response.json()) as { translations: Record<string, string> };
  return body.translations;
}

interface I18nValue {
  t: Translator;
  lang: Language;
  ready: boolean;
}

/**
 * Default returns the key, so a component rendered before the catalog
 * arrives (or in a test) degrades to key text rather than crashing on a
 * missing provider.
 */
export const I18nContext = createContext<I18nValue>({
  t: (key) => key,
  lang: "uz",
  ready: false,
});

export function useT(): Translator {
  return useContext(I18nContext).t;
}

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}
