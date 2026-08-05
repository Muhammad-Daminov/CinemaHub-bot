/**
 * Restricts the catalog to titles that actually have a file in one audio
 * language. Server-side: the backend narrows the same playable-file
 * EXISTS it already runs, so "has a Russian track" and "is watchable"
 * stay one question.
 *
 * A horizontal chip strip rather than a <select>, because it needs to
 * read as a live filter on the catalog rather than a form field, and it
 * follows the same no-scrollbar overflow pattern as MovieRow.
 */
import { useT } from "../lib/i18n";
import type { AudioLanguageFilter } from "../types/movie";

const OPTIONS: AudioLanguageFilter[] = ["uz_dub", "uz_sub", "ru", "en", "original"];

interface Props {
  value: AudioLanguageFilter | null;
  onChange: (value: AudioLanguageFilter | null) => void;
}

export function AudioFilter({ value, onChange }: Props) {
  const t = useT();

  const chip = (active: boolean) =>
    `shrink-0 rounded-full px-3 py-1.5 font-body text-xs transition-colors ${
      active ? "bg-marquee text-on-marquee" : "border border-surface-hi bg-surface text-ink-dim"
    }`;

  return (
    <div className="px-4 pt-3">
      <p className="mb-1.5 font-mono text-[11px] uppercase tracking-wider text-ink-dim">
        {t("app.audio_filter")}
      </p>
      <div className="no-scrollbar flex gap-2 overflow-x-auto pb-1">
        <button onClick={() => onChange(null)} className={chip(value === null)}>
          {t("app.audio_all")}
        </button>
        {OPTIONS.map((option) => (
          <button
            key={option}
            onClick={() => onChange(option)}
            className={chip(value === option)}
          >
            {t(`audio.${option}`)}
          </button>
        ))}
      </div>
    </div>
  );
}
