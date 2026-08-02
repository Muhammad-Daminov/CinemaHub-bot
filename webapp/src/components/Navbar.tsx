import { Clapperboard } from "lucide-react";
import { SearchBar } from "./SearchBar";
import { ThemeToggle } from "./ThemeToggle";

interface Props {
  onSearch: (query: string) => void;
  isDark: boolean;
  onToggleTheme: () => void;
}

export function Navbar({ onSearch, isDark, onToggleTheme }: Props) {
  return (
    <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-surface-hi bg-bg/90 px-4 py-3 backdrop-blur">
      <div className="flex shrink-0 items-center gap-1.5 font-display text-lg font-semibold tracking-wide text-ink">
        <Clapperboard size={20} className="text-marquee" strokeWidth={2.25} />
        CinemaHub
      </div>
      <SearchBar onSearch={onSearch} />
      <ThemeToggle isDark={isDark} onToggle={onToggleTheme} />
    </header>
  );
}
