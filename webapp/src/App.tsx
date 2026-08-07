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
        .then((movies) => setRowMovies((current) => ({ ...current, [row.key]: movies })))
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
            .then((movies) => setRowMovies((current) => ({ ...current, [row.key]: movies })))
            .catch(() => undefined);
        }
      })
      .catch(() => setCollectionRows([]));
  }, [baseRows, audioLanguage, t]);

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
        .then(setSearchResults)
        .catch(() => setSearchResults([]));
    },
    [audioLanguage],
  );

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
        <SettingsPage profile={profile} onChangeLanguage={handleChangeLanguage} />
      ) : (
        <>
          <Navbar onSearch={handleSearch} isDark={isDark} onToggleTheme={() => setIsDark((d) => !d)} />

          {isSearching ? (
            <>
              <AudioFilter value={audioLanguage} onChange={setAudioLanguage} />
              <div className="grid grid-cols-3 gap-3 p-4 sm:grid-cols-4">
                {searchResults.map((movie) => (
                  <MovieCard key={movie.id} movie={movie} onSelect={setSelectedMovie} />
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
              {homeRows.map((row) => (
                <MovieRow
                  key={row.key}
                  title={row.title}
                  movies={rowMovies[row.key] ?? []}
                  onSelect={setSelectedMovie}
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
