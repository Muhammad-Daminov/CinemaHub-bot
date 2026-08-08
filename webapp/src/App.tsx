import { Home, Settings, Shield } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminDashboard } from "./admin/AdminDashboard";
import { AudioFilter } from "./components/AudioFilter";
import { HeroBanner } from "./components/HeroBanner";
import { I18nProvider } from "./components/I18nProvider";
import { LanguagePicker } from "./components/LanguagePicker";
import { MovieCard } from "./components/MovieCard";
import { MovieDetailSheet } from "./components/MovieDetailSheet";
import { MovieRow } from "./components/MovieRow";
import { PlansSheet } from "./components/PlansSheet";
import { Navbar } from "./components/Navbar";
import { SettingsPage } from "./components/SettingsPage";
import { Toast } from "./components/Toast";
import { api, ApiError } from "./lib/api";
import { useT, type Language, type Translator } from "./lib/i18n";
import { getColorScheme, initTelegramApp, onThemeChange } from "./lib/telegram";
import type {
  AudioLanguageFilter,
  Episode,
  Movie,
  MovieContentType,
  UserProfile,
} from "./types/movie";

/** A home row: a translated heading plus the request that fills it. */
interface RowSpec {
  key: string;
  title: string;
  load: () => Promise<Movie[]>;
}

const ROW_LIMIT = 20;

const TYPE_ROWS: { type: MovieContentType; labelKey: string }[] = [
  { type: "serial", labelKey: "app.row_serial" },
  { type: "anime", labelKey: "app.row_anime" },
  { type: "multfilm", labelKey: "app.row_multfilm" },
  { type: "drama", labelKey: "app.row_drama" },
];

/**
 * Row definitions depend on both the translator and the active audio
 * filter, so they're built per render rather than being module-level
 * constants. Each row still owns its own request — nothing waits on
 * anything else, which is why the page paints progressively.
 */
function buildRows(t: Translator, audio: AudioLanguageFilter | null): RowSpec[] {
  const audioParam = audio ?? undefined;
  return [
    {
      key: "recommended",
      title: t("app.row_recommended"),
      load: () => api.recommended(ROW_LIMIT, audioParam),
    },
    {
      key: "continue",
      title: t("app.row_continue"),
      load: () => api.continueWatching(ROW_LIMIT, audioParam),
    },
    {
      key: "newest",
      title: t("app.row_newest"),
      load: () => api.listMovies({ limit: ROW_LIMIT, audio_language: audioParam }),
    },
    { key: "top", title: t("app.row_top"), load: () => api.topMovies(ROW_LIMIT, audioParam) },
    ...TYPE_ROWS.map(({ type, labelKey }) => ({
      key: `type:${type}`,
      title: t(labelKey),
      load: () =>
        api.listMovies({ content_type: type, limit: ROW_LIMIT, audio_language: audioParam }),
    })),
  ];
}

export default function App() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [profileLoaded, setProfileLoaded] = useState(false);

  useEffect(() => {
    initTelegramApp();
  }, []);

  useEffect(() => {
    api
      .me()
      .then(setProfile)
      .catch(() => setProfile(null))
      .finally(() => setProfileLoaded(true));
  }, []);

  // Everything below the provider can call t(); the provider itself needs
  // to know which catalog to fetch, which is why the profile loads first.
  if (!profileLoaded) return <div className="min-h-full bg-bg" />;

  return (
    <I18nProvider lang={profile?.language ?? "uz"}>
      <Shell profile={profile} setProfile={setProfile} />
    </I18nProvider>
  );
}

function Shell({
  profile,
  setProfile,
}: {
  profile: UserProfile | null;
  setProfile: (profile: UserProfile) => void;
}) {
  const t = useT();
  const [isDark, setIsDark] = useState(true);
  const [view, setView] = useState<"home" | "settings" | "admin">("home");
  const [rowMovies, setRowMovies] = useState<Record<string, Movie[]>>({});
  const [collectionRows, setCollectionRows] = useState<RowSpec[]>([]);
  const [audioLanguage, setAudioLanguage] = useState<AudioLanguageFilter | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Movie[]>([]);
  const [selectedMovie, setSelectedMovie] = useState<Movie | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: "success" | "error" } | null>(null);
  const [plansOpen, setPlansOpen] = useState(false);
  // One set for the whole app. Rows share titles, so per-row state would
  // let the same film show a filled heart in one row and an empty one in
  // the next.
  const [favorites, setFavorites] = useState<Set<number>>(new Set());
  const [savedMovies, setSavedMovies] = useState<Movie[]>([]);

  /**
   * Reconciles the set against a freshly loaded list.
   *
   * Adds *and* removes, rather than only adding: a title unsaved on
   * another device would otherwise keep its filled heart here forever,
   * because a union can never learn that something stopped being saved.
   */
  const absorbFavorites = useCallback((movies: Movie[]) => {
    setFavorites((current) => {
      const next = new Set(current);
      for (const movie of movies) {
        if (movie.is_favorite) next.add(movie.id);
        else next.delete(movie.id);
      }
      return next;
    });
  }, []);

  const loadSaved = useCallback(() => {
    api
      .favorites()
      .then((movies) => {
        setSavedMovies(movies);
        absorbFavorites(movies);
      })
      .catch(() => setSavedMovies([]));
  }, [absorbFavorites]);

  useEffect(() => {
    loadSaved();
  }, [loadSaved]);


  // Comes straight from /api/auth/me. This used to be discovered by calling
  // an admin-only route and reading the status code, which meant a 403 on
  // every load for every ordinary user.
  const isAdmin = profile?.is_admin ?? false;

  useEffect(() => {
    const syncTheme = () => setIsDark(getColorScheme() === "dark");
    syncTheme();
    onThemeChange(syncTheme);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark);
  }, [isDark]);

  const baseRows = useMemo(() => buildRows(t, audioLanguage), [t, audioLanguage]);

  // Refetches when the audio filter changes; each row paints as it lands.
  // A row that fails stays empty and MovieRow hides itself, so one broken
  // endpoint costs its own row rather than the page.
  useEffect(() => {
    const audioParam = audioLanguage ?? undefined;
    const allRow: RowSpec = {
      key: "all",
      title: t("app.row_all"),
      load: () => api.listMovies({ limit: 30, audio_language: audioParam }),
    };

    for (const row of [...baseRows, allRow]) {
      row
        .load()
        .then((movies) => {
          setRowMovies((current) => ({ ...current, [row.key]: movies }));
          absorbFavorites(movies);
        })
        .catch(() => undefined);
    }

    api
      .collections()
      .then((collections) => {
        const rows = collections
          .filter((collection) => collection.title_count > 0)
          .map<RowSpec>((collection) => ({
            key: `collection:${collection.id}`,
            title: collection.name,
            load: () =>
              api.listMovies({
                collection_id: collection.id,
                limit: ROW_LIMIT,
                audio_language: audioParam,
              }),
          }));
        setCollectionRows(rows);
        for (const row of rows) {
          row
            .load()
            .then((movies) => {
              setRowMovies((current) => ({ ...current, [row.key]: movies }));
              absorbFavorites(movies);
            })
            .catch(() => undefined);
        }
      })
      .catch(() => setCollectionRows([]));
  }, [baseRows, audioLanguage, t, absorbFavorites]);

  useEffect(() => {
    if (!toast) return;
    const timeout = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(timeout);
  }, [toast]);

  const handleSearch = useCallback(
    (query: string) => {
      setSearchQuery(query);
      if (!query) {
        setSearchResults([]);
        return;
      }
      api
        .searchMovies(query, audioLanguage ?? undefined)
        .then((results) => {
          setSearchResults(results);
          absorbFavorites(results);
        })
        .catch(() => setSearchResults([]));
    },
    [audioLanguage, absorbFavorites],
  );

  /**
   * Optimistic: the heart fills on tap and is put back if the server
   * disagrees. A toggle that waits for a round trip on a phone connection
   * reads as a dead button, and the usual response to that is a second tap
   * — which would toggle it straight back.
   */
  const handleToggleFavorite = async (movie: Movie) => {
    const wasSaved = favorites.has(movie.id);
    setFavorites((current) => {
      const next = new Set(current);
      if (wasSaved) next.delete(movie.id);
      else next.add(movie.id);
      return next;
    });

    try {
      const result = await api.toggleFavorite(movie.id);
      setFavorites((current) => {
        const next = new Set(current);
        if (result.is_favorite) next.add(movie.id);
        else next.delete(movie.id);
        return next;
      });
      loadSaved();
    } catch {
      setFavorites((current) => {
        const next = new Set(current);
        if (wasSaved) next.add(movie.id);
        else next.delete(movie.id);
        return next;
      });
      setToast({ message: t("app.generic_error"), tone: "error" });
    }
  };

  const handleWatch = async (movie: Movie, episode?: Episode) => {
    setSelectedMovie(null);
    try {
      const response = await api.watchMovie(movie.id, episode?.id);
      setToast({ message: response.message, tone: "success" });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : t("app.generic_error");
      setToast({ message, tone: "error" });
    }
  };

  const handleChangeLanguage = async (language: Language) => {
    const updated = await api.setLanguage(language);
    setProfile(updated);
    setToast({ message: t("app.settings_language_saved"), tone: "success" });
  };

  // Never asked, in the bot or here — pick a language before anything else.
  if (profile && !profile.language_selected) {
    return <LanguagePicker onPick={handleChangeLanguage} />;
  }

  // Every endpoint below /auth/me refuses a banned account, so without
  // this the app would render an empty catalog and look broken rather
  // than blocked.
  if (profile?.is_banned) {
    return (
      <div className="flex min-h-full flex-col items-center justify-center gap-2 bg-bg p-8 text-center">
        <h1 className="font-display text-lg font-semibold text-ink">{t("app.blocked_title")}</h1>
        <p className="font-body text-sm text-ink-dim">{t("app.blocked_text")}</p>
      </div>
    );
  }

  const bannerMovies = (() => {
    const pool: Movie[] = [];
    const seen = new Set<number>();
    for (const movie of [...(rowMovies.newest ?? []), ...(rowMovies.top ?? [])]) {
      if (seen.has(movie.id)) continue;
      seen.add(movie.id);
      pool.push(movie);
    }
    return pool.slice(0, 5);
  })();

  const homeRows = [
    ...baseRows,
    ...collectionRows,
    {
      key: "all",
      title: t("app.row_all"),
      load: () => api.listMovies({ limit: 30 }),
    },
  ];
  const isSearching = searchQuery.length > 0;

  if (view === "admin" && isAdmin) {
    return (
      <div className="min-h-full bg-bg text-ink">
        <AdminDashboard permissions={profile?.permissions ?? []} isSuperAdmin={profile?.is_super_admin ?? false} />
        <BottomNav view={view} onChange={setView} isAdmin={isAdmin} />
      </div>
    );
  }

  return (
    <div className="min-h-full bg-bg pb-20 text-ink">
      {view === "settings" ? (
        <SettingsPage
          profile={profile}
          onChangeLanguage={handleChangeLanguage}
          onOpenPlans={() => setPlansOpen(true)}
        />
      ) : (
        <>
          <Navbar onSearch={handleSearch} isDark={isDark} onToggleTheme={() => setIsDark((d) => !d)} />

          {isSearching ? (
            <>
              <AudioFilter value={audioLanguage} onChange={setAudioLanguage} />
              <div className="grid grid-cols-3 gap-3 p-4 sm:grid-cols-4">
                {searchResults.map((movie) => (
                  <MovieCard
                    key={movie.id}
                    movie={movie}
                    onSelect={setSelectedMovie}
                    isFavorite={favorites.has(movie.id)}
                    onToggleFavorite={handleToggleFavorite}
                  />
                ))}
                {searchResults.length === 0 && (
                  <p className="col-span-full py-10 text-center text-sm text-ink-dim">
                    {t("app.nothing_found")}
                  </p>
                )}
              </div>
            </>
          ) : (
            <>
              <HeroBanner movies={bannerMovies} onWatch={handleWatch} onDetails={setSelectedMovie} />
              <AudioFilter value={audioLanguage} onChange={setAudioLanguage} />
              {/* First row when it has anything: what the viewer saved is
                  what they came back for. MovieRow hides itself when empty,
                  so a user with no favourites never sees it. */}
              <MovieRow
                title={t("app.row_favorites")}
                movies={savedMovies}
                onSelect={setSelectedMovie}
                favorites={favorites}
                onToggleFavorite={handleToggleFavorite}
              />
              {homeRows.map((row) => (
                <MovieRow
                  key={row.key}
                  title={row.title}
                  movies={rowMovies[row.key] ?? []}
                  onSelect={setSelectedMovie}
                  favorites={favorites}
                  onToggleFavorite={handleToggleFavorite}
                />
              ))}
            </>
          )}
        </>
      )}

      {selectedMovie && (
        <MovieDetailSheet
          movie={selectedMovie}
          onClose={() => setSelectedMovie(null)}
          onWatch={handleWatch}
          onSelectSimilar={setSelectedMovie}
          audioLanguage={audioLanguage}
          isFavorite={favorites.has(selectedMovie.id)}
          onToggleFavorite={handleToggleFavorite}
        />
      )}
      {plansOpen && (
        <PlansSheet
          onClose={() => {
            setPlansOpen(false);
            // Balance and subscription both move on purchase.
            void api.me().then(setProfile).catch(() => undefined);
          }}
          onToast={(message, tone) => setToast({ message, tone })}
        />
      )}
      {toast && <Toast message={toast.message} tone={toast.tone} />}
      <BottomNav view={view} onChange={setView} isAdmin={isAdmin} />
    </div>
  );
}

function BottomNav({
  view,
  onChange,
  isAdmin,
}: {
  view: "home" | "settings" | "admin";
  onChange: (view: "home" | "settings" | "admin") => void;
  isAdmin: boolean;
}) {
  const t = useT();
  const items = [
    { id: "home" as const, labelKey: "app.nav_home", icon: Home },
    { id: "settings" as const, labelKey: "app.nav_settings", icon: Settings },
    ...(isAdmin ? [{ id: "admin" as const, labelKey: "app.nav_admin", icon: Shield }] : []),
  ];

  return (
    <nav className="fixed inset-x-0 bottom-0 z-30 flex border-t border-surface-hi bg-bg/95 pb-[env(safe-area-inset-bottom)] backdrop-blur">
      {items.map((item) => {
        const Icon = item.icon;
        const active = view === item.id;
        return (
          <button
            key={item.id}
            onClick={() => onChange(item.id)}
            className={`flex flex-1 flex-col items-center gap-0.5 py-2.5 font-body text-[11px] ${
              active ? "text-marquee" : "text-ink-dim"
            }`}
          >
            <Icon size={19} strokeWidth={active ? 2.4 : 2} />
            {t(item.labelKey)}
          </button>
        );
      })}
    </nav>
  );
}
