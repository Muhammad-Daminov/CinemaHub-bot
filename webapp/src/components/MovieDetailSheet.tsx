import { Play, Star, X } from "lucide-react";
import type { Movie } from "../types/movie";

interface Props {
  movie: Movie;
  onClose: () => void;
  onWatch: (movie: Movie) => void;
}

export function MovieDetailSheet({ movie, onClose, onWatch }: Props) {
  return (
    <div className="fixed inset-0 z-30 flex items-end bg-black/60" onClick={onClose}>
      {/*
        The sheet and the admin BottomNav are both z-30 and the nav renders
        after it, so the nav paints over the sheet's bottom edge and swallows
        the watch button. Clear it with the same 5rem allowance App.tsx uses
        for the nav (`pb-20`), plus the home-indicator inset — index.html sets
        viewport-fit=cover, so env() actually resolves here instead of to 0.
      */}
      <div
        onClick={(event) => event.stopPropagation()}
        className="w-full rounded-t-2xl bg-surface p-4 pb-[calc(5rem_+_env(safe-area-inset-bottom))] shadow-2xl"
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <h2 className="font-display text-xl font-semibold text-ink">{movie.title}</h2>
          <button onClick={onClose} aria-label="Close" className="shrink-0 text-ink-dim hover:text-ink">
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
          {movie.genres && <span>{movie.genres.join(", ")}</span>}
        </div>

        {movie.description && (
          <p className="mb-4 text-sm leading-relaxed text-ink-dim">{movie.description}</p>
        )}

        <button
          onClick={() => onWatch(movie)}
          className="flex w-full items-center justify-center gap-1.5 rounded-full bg-marquee py-3 font-semibold text-on-marquee shadow-marquee transition-transform active:scale-95"
        >
          <Play size={16} fill="currentColor" />
          Tomosha qilish
        </button>
      </div>
    </div>
  );
}
