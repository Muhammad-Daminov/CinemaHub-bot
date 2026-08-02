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
import type { Movie } from "./types/movie";

export default function App() {
  const [isDark, setIsDark] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [view, setView] = useState<"home" | "admin">("home");
  const [topMovies, setTopMovies] = useState<Movie[]>([]);
  const [catalogMovies, setCatalogMovies] = useState<Movie[]>([]);
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

  useEffect(() => {
    api.topMovies(10).then(setTopMovies).catch(() => setTopMovies([]));
    api.listMovies({ limit: 30 }).then(setCatalogMovies).catch(() => setCatalogMovies([]));
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

  const heroMovie = topMovies[0];
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
          {heroMovie && (
            <HeroBanner movie={heroMovie} onWatch={handleWatch} onDetails={setSelectedMovie} />
          )}
          <MovieRow title="Top hafta" movies={topMovies} onSelect={setSelectedMovie} />
          <MovieRow title="Barcha filmlar" movies={catalogMovies} onSelect={setSelectedMovie} />
        </>
      )}

      {selectedMovie && (
        <MovieDetailSheet movie={selectedMovie} onClose={() => setSelectedMovie(null)} onWatch={handleWatch} />
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
