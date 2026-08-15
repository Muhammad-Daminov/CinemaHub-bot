/**
 * Loads the catalog for `lang` and publishes a bound t() to the tree.
 *
 * Kept in .tsx and separate from lib/i18n.ts because that file is plain
 * TypeScript the rest of the app imports freely; only this piece needs
 * JSX.
 *
 * **Children do not render until the catalog has settled.** They used to
 * render immediately against an empty catalog, which cost a full second
 * copy of the home screen on every cold load: `value` is memoised on the
 * catalog, so when the fetch landed `t` took a new identity, and
 * everything keyed on `t` — `buildRows` in App, and the effect that
 * depends on it — re-ran and re-issued every catalog request.
 *
 * Gating here rather than in App because this is the only component that
 * knows when the catalog has settled, and because a consumer rendered
 * against the empty catalog would paint raw keys for a frame before
 * swapping to real labels.
 *
 * "Settled" deliberately includes *failed*: the catch below still opens
 * the gate, so a translation outage costs key-text labels — the previous
 * behaviour — and never a blank screen.
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

  // Same placeholder App uses while the profile loads, so the two gates
  // read as one uninterrupted blank rather than a flash between them.
  if (!ready) return <div className="min-h-full bg-bg" />;

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
