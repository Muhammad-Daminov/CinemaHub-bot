import { Home, Settings } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AdminDashboard } from "./admin/AdminDashboard";
import { HeroBanner } from "./components/HeroBanner";
import { MovieCard } from "./components/MovieCard";
import { MovieDetailSheet } from "./components/MovieDetailSheet";
import { MovieRow } from "./components/MovieRow";
import { Navbar } from "./components/Navbar";
import { Toast } from "./components/Toast";
import { adminApi, api, ApiError } from "./lib/api";
import { getColorScheme, initTelegramApp, onThemeChange } from "./lib/telegram";
import type { Movie, MovieContentType } from "./types/movie";

/** A home row: a heading plus the request that fills it. */
interface RowSpec {
  key: string;
  title: string;
  load: () => Promise<Movie[]>;
}

const ROW_LIMIT = 20;

/**
 * Fixed rows, in display order. Collection rows are appended after
 * /movies/collections resolves, so they can't be listed here.
 *
 * Each row owns its own request. Nothing waits on anything else — a slow
 * "Siz uchun" must not hold back "Top hafta", which is the whole reason
 * the page isn't loaded as one blocking Promise.all.
 */
const TYPE_ROWS: { type: MovieContentType; title: string }[] = [
  { type: "serial", title: "Seriallar" },
  { type: "anime", title: "Animelar" },
  { type: "multfilm", title: "Multfilmlar" },
  { type: "drama", title: "Dramalar" },
];

const BASE_ROWS: RowSpec[] = [
  { key: "recommended", title: "Siz uchun", load: () => api.recommended(ROW_LIMIT) },
  { key: "continue", title: "Davom ettirish", load: () => api.continueWatching(ROW_LIMIT) },
  { key: "newest", title: "Yangi qo'shilgan", load: () => api.listMovies({ limit: ROW_LIMIT }) },
  { key: "top", title: "Top hafta", load: () => api.topMovies(ROW_LIMIT) },
  ...TYPE_ROWS.map(({ type, title }) => ({
    key: `type:${type}`,
    title,
    load: () => api.listMovies({ content_type: type, limit: ROW_LIMIT }),
  })),
];

const ALL_ROW: RowSpec = {
  key: "all",
  title: "Barcha filmlar",
  load: () => api.listMovies({ limit: 30 }),
};

export default function App() {
  const [isDark, setIsDark] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [view, setView] = useState<"home" | "admin">("home");
  const [rowMovies, setRowMovies] = useState<Record<string, Movie[]>>({});
  const [collectionRows, setCollectionRows] = useState<RowSpec[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Movie[]>([]);
  const [selectedMovie, setSelectedMovie] = useState<Movie | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: "success" | "error" } | null>(null);

  useEffect(() => {
    initTelegramApp();
    const syncTheme = () => setIsDark(getColorScheme() === "dark");
    syncTheme();
    onThemeChange(syncTheme);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark);
  }, [isDark]);

  // Fire every row independently and paint each as it lands. A row that
  // fails just stays empty, and MovieRow hides itself — one broken
  // endpoint costs its own row, not the page.
  useEffect(() => {
    for (const row of [...BASE_ROWS, ALL_ROW]) {
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
            load: () => api.listMovies({ collection_id: collection.id, limit: ROW_LIMIT }),
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
  }, []);

  // /api/auth/me carries no admin flag, so we probe an admin-only route
  // instead: 200 means admin, 403 means an ordinary user.
  useEffect(() => {
    adminApi
      .stats()
      .then(() => setIsAdmin(true))
      .catch(() => setIsAdmin(false));
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timeout = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(timeout);
  }, [toast]);

  const handleSearch = useCallback((query: string) => {
    setSearchQuery(query);
    if (!query) {
      setSearchResults([]);
      return;
    }
    api.searchMovies(query).then(setSearchResults).catch(() => setSearchResults([]));
  }, []);

  const handleWatch = async (movie: Movie) => {
    setSelectedMovie(null);
    try {
      const response = await api.watchMovie(movie.id);
      setToast({ message: response.message, tone: "success" });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Xatolik yuz berdi.";
      setToast({ message, tone: "error" });
    }
  };

  // Banner pool: newest first, then the most-watched, deduped. Both rows
  // are already being fetched for the page, so this costs no extra request.
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

  const homeRows = [...BASE_ROWS, ...collectionRows, ALL_ROW];
  const isSearching = searchQuery.length > 0;

  // Admins get a bottom nav to reach the panel; for everyone else the app
  // renders exactly as before, with no extra chrome.
  if (isAdmin && view === "admin") {
    return (
      <div className="min-h-full bg-bg text-ink">
        <AdminDashboard />
        <BottomNav view={view} onChange={setView} />
      </div>
    );
  }

  return (
    <div className={`min-h-full bg-bg text-ink ${isAdmin ? "pb-20" : ""}`}>
      <Navbar onSearch={handleSearch} isDark={isDark} onToggleTheme={() => setIsDark((d) => !d)} />

      {isSearching ? (
        <div className="grid grid-cols-3 gap-3 p-4 sm:grid-cols-4">
          {searchResults.map((movie) => (
            <MovieCard key={movie.id} movie={movie} onSelect={setSelectedMovie} />
          ))}
          {searchResults.length === 0 && (
            <p className="col-span-full py-10 text-center text-sm text-ink-dim">Hech narsa topilmadi.</p>
          )}
        </div>
      ) : (
        <>
          <HeroBanner
            movies={bannerMovies}
            onWatch={handleWatch}
            onDetails={setSelectedMovie}
          />
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

      {selectedMovie && (
        <MovieDetailSheet
          movie={selectedMovie}
          onClose={() => setSelectedMovie(null)}
          onWatch={handleWatch}
          onSelectSimilar={setSelectedMovie}
        />
      )}
      {toast && <Toast message={toast.message} tone={toast.tone} />}
      {isAdmin && <BottomNav view={view} onChange={setView} />}
    </div>
  );
}

function BottomNav({
  view,
  onChange,
}: {
  view: "home" | "admin";
  onChange: (view: "home" | "admin") => void;
}) {
  const items = [
    { id: "home" as const, label: "Asosiy", icon: Home },
    { id: "admin" as const, label: "Admin", icon: Settings },
  ];

  return (
    <nav className="fixed inset-x-0 bottom-0 z-30 flex border-t border-surface-hi bg-bg/95 backdrop-blur">
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
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}
