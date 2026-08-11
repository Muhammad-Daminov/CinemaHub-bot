import { Crown, Heart, Lock, Play, Star, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useT } from "../lib/i18n";
import type { AudioLanguageFilter, Episode, Movie } from "../types/movie";
import { EpisodeSelector } from "./EpisodeSelector";
import { MovieCard } from "./MovieCard";

const SIMILAR_LIMIT = 10;

interface Props {
  movie: Movie;
  onClose: () => void;
  /** `episode` is omitted for a film, which has exactly one. */
  onWatch: (movie: Movie, episode?: Episode) => void;
  onSelectSimilar: (movie: Movie) => void;
  /** The catalog's active audio filter, so this row obeys it like every other. */
  audioLanguage: AudioLanguageFilter | null;
  isFavorite: boolean;
  onToggleFavorite: (movie: Movie) => void;
  /**
   * Opens the app's one subscription sheet. Threaded in from App rather
   * than rendered here so there stays exactly one PlansSheet instance and
   * one place that refreshes state after a purchase.
   */
  onOpenPlans: () => void;
}

export function MovieDetailSheet({
  movie,
  onClose,
  onWatch,
  onSelectSimilar,
  audioLanguage,
  isFavorite,
  onToggleFavorite,
  onOpenPlans,
}: Props) {
  const t = useT();
  const [similar, setSimilar] = useState<Movie[]>([]);

  // Read from the title itself rather than from whether the selector has
  // finished loading. Keying off the selector meant a failed episodes
  // request re-exposed the generic Watch button on a serial — and that
  // button starts episode 1.
  const hasEpisodeChooser = movie.episode_count > 1;

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
          <div className="flex shrink-0 items-center gap-3">
            <button
              onClick={() => onToggleFavorite(movie)}
              aria-label={t(isFavorite ? "app.remove_favorite" : "app.add_favorite")}
              aria-pressed={isFavorite}
              className="transition-transform active:scale-90"
            >
              <Heart
                size={20}
                className={isFavorite ? "text-marquee" : "text-ink-dim"}
                fill={isFavorite ? "currentColor" : "none"}
              />
            </button>
            <button onClick={onClose} aria-label={t("app.close")} className="text-ink-dim hover:text-ink">
              <X size={20} />
            </button>
          </div>
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
          {/* The number a viewer can type in the bot or the search box to
              come straight back here. Shown wherever the title is, because
              a code that is never displayed is a code nobody can use. */}
          {movie.code && (
            <span className="text-ink-dim">
              {t("app.code_label")} {movie.code}
            </span>
          )}
        </div>

        {movie.is_premium && (
          <div
            className={`mb-3 flex items-center gap-2 rounded-xl px-3 py-2 text-xs ${
              movie.is_locked
                ? "bg-surface-hi text-ink"
                : "bg-surface text-marquee"
            }`}
          >
            {movie.is_locked ? <Lock size={14} /> : <Crown size={14} />}
            <span>
              {movie.is_locked ? t("app.premium_locked_hint") : t("app.premium_badge")}
            </span>
          </div>
        )}

        {/*
          The way out of the locked state. Without it the padlock is a dead
          end: the viewer is told a subscription is needed and left to find
          the settings screen on their own.

          It opens the existing sheet and does nothing else — no plan is
          chosen, nothing is reserved, and no request is sent. Opening a
          locked film must never begin a purchase; the buy still happens
          only where it always has, behind the plan's own button.
        */}
        {movie.is_locked && (
          <button
            onClick={onOpenPlans}
            className="mb-4 flex w-full items-center justify-center gap-1.5 rounded-full bg-marquee py-3 font-semibold text-on-marquee shadow-marquee transition-transform active:scale-95"
          >
            <Crown size={16} />
            {t("app.premium_cta")}
          </button>
        )}

        {movie.description && (
          <p className="mb-4 text-sm leading-relaxed text-ink-dim">{movie.description}</p>
        )}

        {/* A locked title offers no play control at all — neither the
            episode rows nor the watch button below. The server would
            refuse either one, so showing them would only produce a tap
            that fails; the notice above says what to do instead. */}
        {!movie.is_locked && (
          <EpisodeSelector movieId={movie.id} onPlay={(episode) => onWatch(movie, episode)} />
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

        {/* Hidden when the episode list is up: each row is its own play
            control, and this button would silently start episode 1. */}
        {!hasEpisodeChooser && !movie.is_locked && (
        <button
          onClick={() => onWatch(movie)}
          className="flex w-full items-center justify-center gap-1.5 rounded-full bg-marquee py-3 font-semibold text-on-marquee shadow-marquee transition-transform active:scale-95"
        >
          <Play size={16} fill="currentColor" />
          {t("app.watch")}
        </button>
        )}
      </div>
    </div>
  );
}
