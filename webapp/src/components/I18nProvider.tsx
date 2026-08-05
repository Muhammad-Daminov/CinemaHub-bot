/**
 * Loads the catalog for `lang` and publishes a bound t() to the tree.
 *
 * Kept in .tsx and separate from lib/i18n.ts because that file is plain
 * TypeScript the rest of the app imports freely; only this piece needs
 * JSX.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { I18nContext, fetchTranslations, translate, type Language } from "../lib/i18n";

export function I18nProvider({ lang, children }: { lang: Language; children: ReactNode }) {
  const [catalog, setCatalog] = useState<Record<string, string>>({});
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let current = true;
    fetchTranslations(lang)
      .then((translations) => {
        if (!current) return;
        setCatalog(translations);
        setReady(true);
      })
      // An empty catalog still renders: translate() falls back to the key,
      // so a failed fetch costs readable labels, not a blank screen.
      .catch(() => current && setReady(true));
    return () => {
      current = false;
    };
  }, [lang]);

  const value = useMemo(
    () => ({
      t: (key: string, params?: Record<string, string | number>) =>
        translate(catalog, key, params),
      lang,
      ready,
    }),
    [catalog, lang, ready],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
