import { Info, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { useT } from "../lib/i18n";
import type { BannerSlide, Movie } from "../types/movie";

interface Props {
  movies: Movie[];
  onWatch: (movie: Movie) => void;
  onDetails: (movie: Movie) => void;
  /**
   * Admin campaigns, already resolved for this viewer by the backend.
   * Optional: with none configured the carousel behaves exactly as it did
   * before, deriving slides from the newest and top rows.
   */
  slides?: BannerSlide[];
}

const ROTATE_MS = 6000;

/**
 * Someone who has asked not to be shown motion should not get a banner
 * that reshuffles itself under their thumb. Tracked live rather than read
 * once, since Telegram's in-app browser inherits the OS setting and the
 * user can change it while the app is open.
 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(query.matches);
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  return reduced;
}

export function HeroBanner({ movies, onWatch, onDetails, slides }: Props) {
  const t = useT();
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const reducedMotion = usePrefersReducedMotion();

  // Rows arrive progressively, so the slide list grows after first paint.
  // Clamping beats resetting to 0 — it keeps the current slide if it's
  // still in range instead of yanking the user back to the start.
  useEffect(() => {
    setIndex((current) => (current < movies.length ? current : 0));
  }, [movies.length]);

  useEffect(() => {
    if (paused || reducedMotion || movies.length < 2) return;
    const timer = setInterval(
      () => setIndex((current) => (current + 1) % movies.length),
      ROTATE_MS,
    );
    return () => clearInterval(timer);
  }, [paused, reducedMotion, movies.length]);

  const active = movies[index];
  const campaign = slides?.[index];
  if (!active) return null;

  return (
    <div
      className="relative aspect-[3/4] w-full overflow-hidden sm:aspect-[16/9]"
      // Any touch means they're engaging with this slide; stop moving it.
      onPointerDown={() => setPaused(true)}
    >
      {movies.map((movie, slide) =>
        movie.poster_url ? (
          <img
            key={movie.id}
            src={movie.poster_url}
            alt=""
            className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-700 ${
              slide === index ? "opacity-100" : "opacity-0"
            }`}
          />
        ) : null,
      )}

      <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/40 to-transparent" />

      <div className="absolute inset-x-0 bottom-0 p-4 pb-6">
        {campaign?.label_key ? (
          // A campaign label ("Coming soon") replaces the genre line and is
          // rendered from a locale key, so it reads in the viewer's language.
          <p className="mb-1 inline-block rounded-full bg-marquee px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-on-marquee">
            {t(campaign.label_key)}
          </p>
        ) : (
          active.genres &&
          active.genres.length > 0 && (
            <p className="mb-1 font-mono text-xs uppercase tracking-wider text-marquee">
              {active.genres.slice(0, 3).map((g) => t(`genre.${g}`)).join(" · ")}
            </p>
          )
        )}
        {/* Headline and subtitle are plain text — rendered as text, never
            as markup, and the backend refuses angle brackets outright. */}
        <h1 className="mb-1 font-display text-3xl font-semibold leading-tight text-ink drop-shadow-lg sm:text-4xl">
          {campaign?.headline || active.title}
        </h1>
        {campaign?.subtitle && (
          <p className="mb-2 font-body text-sm text-ink-dim drop-shadow">{campaign.subtitle}</p>
        )}
        <div className="flex gap-2">
          <button
            // A serial has no single 'play' — open the sheet so the
            // viewer picks an episode, rather than silently starting #1.
            onClick={() => (active.episode_count > 1 ? onDetails(active) : onWatch(active))}
            className="flex items-center gap-1.5 rounded-full bg-marquee px-5 py-2 font-body text-sm font-semibold text-on-marquee shadow-marquee transition-transform active:scale-95"
          >
            <Play size={16} fill="currentColor" />
            {t("app.watch")}
          </button>
          <button
            onClick={() => onDetails(active)}
            className="flex items-center gap-1.5 rounded-full bg-surface-hi/80 px-5 py-2 text-sm font-medium text-ink backdrop-blur transition-transform active:scale-95"
          >
            <Info size={16} />
            {t("app.details")}
          </button>
        </div>

        {movies.length > 1 && (
          <div className="mt-3 flex gap-1.5">
            {movies.map((movie, slide) => (
              <button
                key={movie.id}
                onClick={() => {
                  setIndex(slide);
                  setPaused(true);
                }}
                aria-label={`${slide + 1}-banner`}
                aria-current={slide === index}
                className={`h-1.5 rounded-full transition-all ${
                  slide === index ? "w-5 bg-marquee" : "w-1.5 bg-ink-dim/60"
                }`}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
