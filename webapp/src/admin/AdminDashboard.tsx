/**
 * Admin panel shell.
 *
 * Tab state is plain useState — the project has no router, and the panel
 * is a leaf of the Mini App rather than a separate destination, so there
 * is nothing to keep in the URL. The tab strip scrolls horizontally on
 * narrow screens using the same no-scrollbar pattern as MovieRow.
 */
import { useState } from "react";
import { CardsPanel } from "./CardsPanel";
import { ContentPanel } from "./ContentPanel";
import { PromoPanel } from "./PromoPanel";
import { ReceiptsPanel } from "./ReceiptsPanel";
import { StatsPanel } from "./StatsPanel";
import { UploadsPanel } from "./UploadsPanel";
import { UsersPanel } from "./UsersPanel";

type TabId = "stats" | "content" | "uploads" | "receipts" | "cards" | "promo" | "users";

const TABS: { id: TabId; label: string }[] = [
  { id: "stats", label: "📊 Statistika" },
  { id: "content", label: "🎬 Kontent" },
  { id: "uploads", label: "📥 Yuklanganlar" },
  { id: "receipts", label: "💳 To'lovlar" },
  { id: "cards", label: "🎴 Kartalar" },
  { id: "promo", label: "🎟️ Promokod" },
  { id: "users", label: "👥 Foydalanuvchilar" },
];

export function AdminDashboard() {
  const [tab, setTab] = useState<TabId>("stats");

  return (
    <div className="pb-24">
      <div className="sticky top-0 z-10 border-b border-surface-hi bg-bg/95 backdrop-blur">
        <div className="no-scrollbar flex gap-2 overflow-x-auto px-4 py-3">
          {TABS.map((item) => (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={`shrink-0 rounded-full px-3 py-1.5 font-body text-xs font-medium transition-colors ${
                tab === item.id
                  ? "bg-marquee text-on-marquee"
                  : "border border-surface-hi bg-surface text-ink-dim"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-4">
        {tab === "stats" && <StatsPanel />}
        {tab === "content" && <ContentPanel />}
        {tab === "uploads" && <UploadsPanel />}
        {tab === "receipts" && <ReceiptsPanel />}
        {tab === "cards" && <CardsPanel />}
        {tab === "promo" && <PromoPanel />}
        {tab === "users" && <UsersPanel />}
      </div>
    </div>
  );
}
