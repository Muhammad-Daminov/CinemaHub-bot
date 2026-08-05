/**
 * First-open language choice.
 *
 * Full-screen and unskippable by design: it is shown only to users whose
 * language_selected is false, i.e. who have never been asked in either
 * the bot or here. Its own labels are each written in their own language,
 * so someone who can't read the current one can still find theirs — the
 * same rule the bot's picker follows.
 */
import { useState } from "react";
import { LANGUAGES, useT, type Language } from "../lib/i18n";

interface Props {
  onPick: (language: Language) => Promise<void>;
}

export function LanguagePicker({ onPick }: Props) {
  const t = useT();
  const [saving, setSaving] = useState<Language | null>(null);

  const choose = async (language: Language) => {
    setSaving(language);
    try {
      await onPick(language);
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-6 bg-bg px-6 text-ink">
      <div className="text-center">
        <h1 className="font-display text-2xl font-semibold">{t("app.pick_language_title")}</h1>
        <p className="mt-2 font-body text-sm text-ink-dim">{t("app.pick_language_hint")}</p>
      </div>

      <div className="flex w-full max-w-xs flex-col gap-3">
        {LANGUAGES.map((option) => (
          <button
            key={option.value}
            onClick={() => choose(option.value)}
            disabled={saving !== null}
            className="rounded-xl border border-surface-hi bg-surface px-4 py-3 text-center font-body text-base text-ink transition-transform active:scale-95 disabled:opacity-50"
          >
            {t(option.labelKey)}
          </button>
        ))}
      </div>
    </div>
  );
}
