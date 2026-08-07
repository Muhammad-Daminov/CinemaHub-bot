/**
 * Season and episode picker for the detail sheet.
 *
 * Exists because the Mini App used to have no concept of episodes at all:
 * pressing Watch on a serial always delivered episode 1, however far
 * through it you were.
 *
 * Shows itself based on how many episodes a title actually has rather
 * than on its content type. A film is a Title with one Episode, so the
 * "one episode" case collapses to nothing rendered and the sheet keeps
 * its plain Watch button — and a mislabelled serial still gets a picker.
 *
 * Episodes arrive paged. Long-running serials carry hundreds, and
 * loading them all to render a list the viewer scrolls a screen of is
 * wasted on both ends of the wire.
 */
import { Check, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useT } from "../lib/i18n";
import type { Episode } from "../types/movie";

interface Props {
  movieId: number;
  onPlay: (episode: Episode) => void;
}

export function EpisodeSelector({ movieId, onPlay }: Props) {
  const t = useT();
  const [seasons, setSeasons] = useState<number[]>([]);
  const [seasonsLoaded, setSeasonsLoaded] = useState(false);
  const [season, setSeason] = useState<number | null>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const sentinel = useRef<HTMLDivElement | null>(null);

  // Season list is fixed per title, so it is fetched once and drives
  // everything below it.
  useEffect(() => {
    let current = true;
    setSeasons([]);
    setSeason(null);
    setSeasonsLoaded(false);
    api
      .seasons(movieId)
      .then((result) => {
        if (!current) return;
        setSeasons(result);
        setSeason(result[0] ?? null);
      })
      .catch(() => current && setSeasons([]))
      // Marked loaded even on failure, so a seasons outage degrades to an
      // unfiltered episode list rather than an empty panel.
      .finally(() => current && setSeasonsLoaded(true));
    return () => {
      current = false;
    };
  }, [movieId]);

  // Reset to the first page whenever the title or season changes,
  // otherwise page 2 of season 1 would be requested for season 2.
  useEffect(() => {
    setEpisodes([]);
    setPage(0);
    setHasMore(false);
  }, [movieId, season]);

  const loadPage = useCallback(
    async (target: number) => {
      setLoading(true);
      try {
        const result = await api.episodes(movieId, season ?? undefined, target);
        setEpisodes((current) =>
          // Replacing on page 0 rather than appending means a season switch
          // can never leave the previous season's episodes on screen.
          target === 0 ? result.episodes : [...current, ...result.episodes],
        );
        setHasMore(result.has_more);
        setPage(target);
      } catch {
        setHasMore(false);
      } finally {
        setLoading(false);
      }
    },
    [movieId, season],
  );

  // Waits for the seasons call to settle. Loading before it returns would
  // fetch an unfiltered page and then immediately refetch it filtered —
  // two requests on every sheet open, for one list.
  useEffect(() => {
    if (!seasonsLoaded) return;
    void loadPage(0);
  }, [loadPage, seasonsLoaded]);

  // Infinite scroll: the sentinel sits below the last row, so it only
  // becomes visible once the viewer has actually reached the end.
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasMore || loading) return;

    const observer = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) void loadPage(page + 1);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, loading, page, loadPage]);

  // A single episode is a film. Nothing to choose, so render nothing and
  // let the sheet's own Watch button handle it. Whether that button is
  // shown is decided by the sheet from the title's episode_count, not
  // from this component's load state.
  if (seasons.length <= 1 && episodes.length <= 1 && !hasMore) return null;

  return (
    <section className="mb-4">
      {seasons.length > 1 && (
        <>
          <h3 className="mb-2 font-display text-sm font-medium tracking-wide text-ink">
            {t("app.seasons_title")}
          </h3>
          <div className="no-scrollbar -mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1">
            {seasons.map((value) => {
              const active = value === season;
              return (
                <button
                  key={value}
                  onClick={() => setSeason(value)}
                  className={`shrink-0 rounded-full px-3 py-1.5 font-body text-xs transition-colors ${
                    active
                      ? "bg-marquee text-on-marquee"
                      : "border border-surface-hi bg-surface text-ink-dim"
                  }`}
                >
                  {t("catalog.season_button", { season: value })}
                </button>
              );
            })}
          </div>
        </>
      )}

      <h3 className="mb-2 font-display text-sm font-medium tracking-wide text-ink">
        {t("app.episodes_title")}
      </h3>

      {episodes.length === 0 && !loading ? (
        <p className="font-body text-sm text-ink-dim">{t("app.no_episodes")}</p>
      ) : (
        <ul className="space-y-1.5">
          {episodes.map((episode) => (
            <li key={episode.id}>
              <button
                onClick={() => onPlay(episode)}
                className="flex w-full items-center gap-3 rounded-xl border border-surface-hi bg-surface px-3 py-2.5 text-left transition-transform active:scale-[0.99]"
              >
                <Play size={14} className="shrink-0 text-marquee" fill="currentColor" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate font-body text-sm text-ink">
                      {episode.name ?? t("catalog.episode_button", { number: episode.number })}
                    </span>
                    {episode.watched && (
                      <Check
                        size={13}
                        className="shrink-0 text-ink-dim"
                        aria-label={t("app.episode_watched")}
                      />
                    )}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 font-mono text-[11px] text-ink-dim">
                    <span>{t("catalog.episode_button", { number: episode.number })}</span>
                    {episode.duration_minutes != null && <span>· {episode.duration_minutes}m</span>}
                    {/* Per-episode audio, so a partly-dubbed serial does not
                        promise a track this episode lacks. */}
                    {episode.audio_languages.length > 0 ? (
                      <span className="truncate">
                        · {episode.audio_languages.map((code) => t(`audio.${code}`)).join(", ")}
                      </span>
                    ) : (
                      <span>· {t("app.audio_none")}</span>
                    )}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div ref={sentinel} className="h-1" />
      {loading && (
        <p className="py-2 text-center font-body text-xs text-ink-dim">{t("app.loading_more")}</p>
      )}
    </section>
  );
}
