import { Info, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { useT } from "../lib/i18n";
import { useAuthedImage } from "../lib/useAuthedImage";
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
 * Autoplay is derived, never latched.
 *
 * The previous implementation held a `paused` boolean that was set to
 * true by `onPointerDown` on the whole banner and **never set back to
 * false anywhere**. Since the banner is the first element in the feed and
 * `pointerdown` fires when a *scroll* begins, the user's first swipe down
 * the page silently ended rotation for the rest of the session.
 *
 * The fix is structural rather than a corrected boolean: there is no
 * pause flag at all. Whether the carousel advances is computed from
 * conditions that can only be true temporarily — reduced motion (an OS
 * setting), document visibility (a browser event), and how many slides
 * exist. Nothing a finger does can put the carousel into a state that
 * only another finger could undo, so this class of bug cannot return by
 * someone forgetting a release path.
 *
 * A deliberate dot tap restarts the countdown rather than stopping it, so
 * the slide the user chose gets a full interval before moving on.
 */

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

/**
 * Whether the document is currently hidden.
 *
 * Advancing slides for a backgrounded Mini App burns cycles nobody sees,
 * and on return the user would land mid-sequence. The release path is the
 * browser's own `visibilitychange`, not a gesture, so this can never
 * strand the carousel: if the event never fires (some WebViews are quiet
 * about it) the value simply stays false and rotation continues, which is
 * the safe direction to fail.
 */
function useDocumentHidden(): boolean {
  const [hidden, setHidden] = useState(
    () => typeof document !== "undefined" && document.hidden,
  );

  useEffect(() => {
    const sync = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  return hidden;
}

export function HeroBanner({ movies, onWatch, onDetails, slides }: Props) {
  const t = useT();
  const [index, setIndex] = useState(0);
  // Bumped by a deliberate interaction to restart the countdown. A number
  // rather than a flag: it can only ever cause the timer to be recreated,
  // never to stop existing.
  const [restartToken, setRestartToken] = useState(0);
  const reducedMotion = usePrefersReducedMotion();
  const hidden = useDocumentHidden();

  // Rows arrive progressively, so the slide list grows after first paint.
  // Clamping beats resetting to 0 — it keeps the current slide if it's
  // still in range instead of yanking the user back to the start.
  useEffect(() => {
    setIndex((current) => (current < movies.length ? current : 0));
  }, [movies.length]);

  // Exactly one interval can exist: the effect creates a single timer and
  // its cleanup clears that same timer, so any dependency change tears the
  // old one down before building the next. `setIndex` takes the updater
  // form, so the callback never closes over a stale index and the timer
  // does not need recreating as the slide advances.
  useEffect(() => {
    if (reducedMotion || hidden || movies.length < 2) return;
    const timer = window.setInterval(
      () => setIndex((current) => (current + 1) % movies.length),
      ROTATE_MS,
    );
    return () => window.clearInterval(timer);
  }, [reducedMotion, hidden, movies.length, restartToken]);

  const active = movies[index];
  const campaign = slides?.[index];
  if (!active) return null;

  return (
    // Deliberately no pointer handler. `pointerdown` fires at the start of
    // a scroll as readily as a tap, and treating it as engagement is what
    // used to kill autoplay on the first swipe.
    <div className="relative aspect-[3/4] w-full overflow-hidden sm:aspect-[16/9]">
      {movies.map((movie, slide) => (
        <HeroSlideImage key={movie.id} movie={movie} visible={slide === index} />
      ))}

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
                  // Restart the countdown so the chosen slide gets a full
                  // interval. It does not stop autoplay — a carousel the
                  // user can permanently freeze by tapping a dot is the
                  // bug this component is being fixed for.
                  setRestartToken((token) => token + 1);
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

/**
 * One cross-fading slide.
 *
 * Its own component because `useAuthedImage` is a hook and the slides are
 * produced by a `.map()`. All slides stay mounted so the opacity
 * transition has something to fade between, so each one resolves its own
 * poster — an uploaded poster needs an authenticated fetch, a TMDB URL
 * does not, and the hook decides which.
 */
function HeroSlideImage({ movie, visible }: { movie: Movie; visible: boolean }) {
  const src = useAuthedImage(movie.poster_url);
  if (!src) return null;
  return (
    <img
      src={src}
      alt=""
      className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-700 ${
        visible ? "opacity-100" : "opacity-0"
      }`}
    />
  );
}
