import { Play, Star, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useT } from "../lib/i18n";
import type { AudioLanguageFilter, Movie } from "../types/movie";
import { MovieCard } from "./MovieCard";

const SIMILAR_LIMIT = 10;

interface Props {
  movie: Movie;
  onClose: () => void;
  onWatch: (movie: Movie) => void;
  onSelectSimilar: (movie: Movie) => void;
  /** The catalog's active audio filter, so this row obeys it like every other. */
  audioLanguage: AudioLanguageFilter | null;
}

export function MovieDetailSheet({
  movie,
  onClose,
  onWatch,
  onSelectSimilar,
  audioLanguage,
}: Props) {
  const t = useT();
  const [similar, setSimilar] = useState<Movie[]>([]);

  useEffect(() => {
    // Clear first: tapping through a chain of similar titles would
    // otherwise show the previous title's row until the new one lands.
    setSimilar([]);
    let current = true;
    api
      .similar(movie.id, SIMILAR_LIMIT, audioLanguage ?? undefined)
      .then((results) => current && setSimilar(results))
      .catch(() => current && setSimilar([]));
    return () => {
      current = false;
    };
  }, [movie.id, audioLanguage]);

  return (
    <div className="fixed inset-0 z-30 flex items-end bg-black/60" onClick={onClose}>
      {/*
        The sheet and the admin BottomNav are both z-30 and the nav renders
        after it, so the nav paints over the sheet's bottom edge and swallows
        the watch button. Clear it with the same 5rem allowance App.tsx uses
        for the nav (`pb-20`), plus the home-indicator inset — index.html sets
        viewport-fit=cover, so env() actually resolves here instead of to 0.

        max-h + scroll because the similar row makes this tall enough to
        exceed a phone screen on a title with a long description.
      */}
      <div
        onClick={(event) => event.stopPropagation()}
        className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-surface p-4 pb-[calc(5rem_+_env(safe-area-inset-bottom))] shadow-2xl"
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <h2 className="font-display text-xl font-semibold text-ink">{movie.title}</h2>
          <button onClick={onClose} aria-label={t("app.close")} className="shrink-0 text-ink-dim hover:text-ink">
            <X size={20} />
          </button>
        </div>

        <div className="mb-3 flex items-center gap-3 font-mono text-xs text-ink-dim">
          {movie.year && <span>{movie.year}</span>}
          {movie.rating != null && (
            <span className="flex items-center gap-0.5 text-marquee">
              <Star size={12} fill="currentColor" />
              {movie.rating.toFixed(1)}
            </span>
          )}
          {movie.genres && <span>{movie.genres.map((g) => t(`genre.${g}`)).join(", ")}</span>}
        </div>

        {movie.description && (
          <p className="mb-4 text-sm leading-relaxed text-ink-dim">{movie.description}</p>
        )}

        {similar.length > 0 && (
          <section className="mb-4">
            <h3 className="mb-2 font-display text-sm font-medium tracking-wide text-ink">{t("app.similar")}</h3>
            <div className="no-scrollbar -mx-4 flex gap-3 overflow-x-auto px-4 pb-1">
              {similar.map((item) => (
                <MovieCard key={item.id} movie={item} onSelect={onSelectSimilar} />
              ))}
            </div>
          </section>
        )}

        <button
          onClick={() => onWatch(movie)}
          className="flex w-full items-center justify-center gap-1.5 rounded-full bg-marquee py-3 font-semibold text-on-marquee shadow-marquee transition-transform active:scale-95"
        >
          <Play size={16} fill="currentColor" />
          {t("app.watch")}
        </button>
      </div>
    </div>
  );
}
