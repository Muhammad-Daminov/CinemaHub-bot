import { Moon, Sun } from "lucide-react";
import { useT } from "../lib/i18n";

interface Props {
  isDark: boolean;
  onToggle: () => void;
}

export function ThemeToggle({ isDark, onToggle }: Props) {
  const t = useT();

  return (
    <button
      onClick={onToggle}
      aria-label={t(isDark ? "app.theme_light" : "app.theme_dark")}
      className="rounded-full p-2 text-ink-dim transition-colors hover:bg-surface-hi hover:text-ink"
    >
      {isDark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
