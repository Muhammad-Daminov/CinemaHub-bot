import { Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useT } from "../lib/i18n";

interface Props {
  onSearch: (query: string) => void;
}

export function SearchBar({ onSearch }: Props) {
  const t = useT();
  const [value, setValue] = useState("");

  useEffect(() => {
    const timeout = setTimeout(() => onSearch(value.trim()), 300);
    return () => clearTimeout(timeout);
  }, [value, onSearch]);

  return (
    <div className="relative flex-1 max-w-xs">
      <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-dim" />
      <input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={t("app.search_placeholder")}
        className="w-full rounded-full border border-surface-hi bg-surface py-2 pl-9 pr-8 text-sm text-ink placeholder:text-ink-dim focus:outline-none focus:ring-2 focus:ring-marquee/60"
      />
      {value && (
        <button
          onClick={() => setValue("")}
          aria-label={t("app.search_clear")}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-dim hover:text-ink"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
