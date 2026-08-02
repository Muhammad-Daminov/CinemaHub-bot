import { Moon, Sun } from "lucide-react";

interface Props {
  isDark: boolean;
  onToggle: () => void;
}

export function ThemeToggle({ isDark, onToggle }: Props) {
  return (
    <button
      onClick={onToggle}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      className="rounded-full p-2 text-ink-dim transition-colors hover:bg-surface-hi hover:text-ink"
    >
      {isDark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
