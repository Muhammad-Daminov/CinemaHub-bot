import { Info, Play } from "lucide-react";
import type { Movie } from "../types/movie";

interface Props {
  movie: Movie;
  onWatch: (movie: Movie) => void;
  onDetails: (movie: Movie) => void;
}

export function HeroBanner({ movie, onWatch, onDetails }: Props) {
  return (
    <div className="relative aspect-[3/4] w-full overflow-hidden sm:aspect-[16/9]">
      {movie.poster_url && (
        <img src={movie.poster_url} alt="" className="h-full w-full object-cover" />
      )}
      <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/40 to-transparent" />
      <div className="absolute inset-x-0 bottom-0 p-4 pb-6">
        {movie.genres && movie.genres.length > 0 && (
          <p className="mb-1 font-mono text-xs uppercase tracking-wider text-marquee">
            {movie.genres.slice(0, 3).join(" · ")}
          </p>
        )}
        <h1 className="mb-3 font-display text-3xl font-semibold leading-tight text-ink drop-shadow-lg sm:text-4xl">
          {movie.title}
        </h1>
        <div className="flex gap-2">
          <button
            onClick={() => onWatch(movie)}
            className="flex items-center gap-1.5 rounded-full bg-marquee px-5 py-2 font-body text-sm font-semibold text-on-marquee shadow-marquee transition-transform active:scale-95"
          >
            <Play size={16} fill="currentColor" />
            Tomosha qilish
          </button>
          <button
            onClick={() => onDetails(movie)}
            className="flex items-center gap-1.5 rounded-full bg-surface-hi/80 px-5 py-2 text-sm font-medium text-ink backdrop-blur transition-transform active:scale-95"
          >
            <Info size={16} />
            Batafsil
          </button>
        </div>
      </div>
    </div>
  );
}
