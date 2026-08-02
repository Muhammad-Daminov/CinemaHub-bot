import { Star } from "lucide-react";
import type { Movie } from "../types/movie";

interface Props {
  movie: Movie;
  onSelect: (movie: Movie) => void;
}

export function MovieCard({ movie, onSelect }: Props) {
  return (
    <button
      onClick={() => onSelect(movie)}
      className="group w-32 shrink-0 text-left sm:w-36"
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-lg bg-surface-hi shadow-sm transition-transform duration-200 ease-out group-hover:-translate-y-1 group-hover:shadow-marquee">
        {movie.poster_url ? (
          <img
            src={movie.poster_url}
            alt={movie.title}
            loading="lazy"
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full items-center justify-center px-2 text-center font-display text-sm text-ink-dim">
            {movie.title}
          </div>
        )}
        {movie.rating != null && (
          <div className="absolute right-1.5 top-1.5 flex items-center gap-0.5 rounded-full bg-bg/80 px-1.5 py-0.5 font-mono text-[11px] text-marquee backdrop-blur">
            <Star size={10} fill="currentColor" />
            {movie.rating.toFixed(1)}
          </div>
        )}
      </div>
      <p className="mt-1.5 truncate text-sm font-medium text-ink">{movie.title}</p>
      <p className="truncate text-xs text-ink-dim">{movie.year ?? ""}</p>
    </button>
  );
}
