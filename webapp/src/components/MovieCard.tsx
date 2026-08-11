import { Crown, Heart, Lock, Star } from "lucide-react";
import { useT } from "../lib/i18n";
import type { Movie } from "../types/movie";

interface Props {
  movie: Movie;
  onSelect: (movie: Movie) => void;
  /**
   * Saved state and its toggle. Optional so the card still works in the
   * places that have no favourites context — the similar-titles row inside
   * a detail sheet, for one — rather than forcing every caller to thread
   * a handler through for a control it does not show.
   */
  isFavorite?: boolean;
  onToggleFavorite?: (movie: Movie) => void;
}

export function MovieCard({ movie, onSelect, isFavorite, onToggleFavorite }: Props) {
  const t = useT();
  const saved = isFavorite ?? movie.is_favorite;

  return (
    <button
      onClick={() => onSelect(movie)}
      className="group w-32 shrink-0 text-left sm:w-36"
    >
      <div
        // The shape comes from the active theme, looked up by name against
        // a fixed radius map — the client never accepts a raw CSS value.
        // Falls back to the compiled-in radius when no theme is set.
        style={{ borderRadius: "var(--radius-card, 0.5rem)" }}
        className="relative aspect-[2/3] overflow-hidden bg-surface-hi shadow-sm transition-transform duration-200 ease-out group-hover:-translate-y-1 group-hover:shadow-marquee"
      >
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
        {/*
          Three states, distinguished on the card itself: a normal title
          gets neither mark, a premium title the viewer can open gets the
          crown, and one they cannot gets the padlock over a dimmed poster.
          The dimming is what makes "locked" readable at a glance without
          reading the badge — and it is presentation only, since the server
          refuses the watch request regardless of what is drawn here.
        */}
        {movie.is_premium && (
          <div
            className="absolute left-1.5 bottom-1.5 flex items-center gap-0.5 rounded-full bg-bg/80 px-1.5 py-0.5 font-mono text-[11px] text-marquee backdrop-blur"
            aria-label={t(movie.is_locked ? "app.premium_locked" : "app.premium_badge")}
          >
            {movie.is_locked ? <Lock size={10} /> : <Crown size={10} />}
          </div>
        )}
        {movie.is_locked && (
          <div className="pointer-events-none absolute inset-0 bg-bg/45" aria-hidden />
        )}
        {movie.rating != null && (
          <div className="absolute right-1.5 top-1.5 flex items-center gap-0.5 rounded-full bg-bg/80 px-1.5 py-0.5 font-mono text-[11px] text-marquee backdrop-blur">
            <Star size={10} fill="currentColor" />
            {movie.rating.toFixed(1)}
          </div>
        )}
        {onToggleFavorite && (
          // A nested <button> is invalid HTML inside the card's own button,
          // so this is a div with a button role — same keyboard and screen
          // reader behaviour, no invalid nesting. stopPropagation keeps a
          // tap on the heart from also opening the detail sheet.
          <div
            role="button"
            tabIndex={0}
            aria-label={t(saved ? "app.remove_favorite" : "app.add_favorite")}
            aria-pressed={saved}
            onClick={(event) => {
              event.stopPropagation();
              onToggleFavorite(movie);
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              event.stopPropagation();
              onToggleFavorite(movie);
            }}
            className="absolute left-1.5 top-1.5 rounded-full bg-bg/80 p-1.5 backdrop-blur transition-transform active:scale-90"
          >
            <Heart
              size={12}
              className={saved ? "text-marquee" : "text-ink-dim"}
              fill={saved ? "currentColor" : "none"}
            />
          </div>
        )}
      </div>
      <p className="mt-1.5 truncate text-sm font-medium text-ink">{movie.title}</p>
      <p className="truncate text-xs text-ink-dim">{movie.year ?? ""}</p>
    </button>
  );
}
