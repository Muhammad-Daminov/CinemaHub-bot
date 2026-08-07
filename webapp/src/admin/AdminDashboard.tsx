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
import { CollectionsPanel } from "./CollectionsPanel";
import { ContentPanel } from "./ContentPanel";
import { PlansPanel } from "./PlansPanel";
import { PromoPanel } from "./PromoPanel";
import { ReceiptsPanel } from "./ReceiptsPanel";
import { StatsPanel } from "./StatsPanel";
import { UploadsPanel } from "./UploadsPanel";
import { AdminsPanel } from "./AdminsPanel";
import { UsersPanel } from "./UsersPanel";

type TabId =
  | "stats"
  | "content"
  | "collections"
  | "uploads"
  | "receipts"
  | "cards"
  | "promo"
  | "users"
  | "plans"
  | "admins";

/**
 * Each tab names the permission that backs it. The API enforces the same
 * permission on every route the tab calls, so hiding it is presentation
 * only — a hidden tab is a courtesy, not the security boundary.
 *
 * `superAdminOnly` marks the administrator management tab: appointing
 * admins is not a grantable capability, so it cannot be expressed as one.
 */
const TABS: { id: TabId; label: string; permission?: string; superAdminOnly?: boolean }[] = [
  { id: "stats", label: "📊 Statistika", permission: "view_analytics" },
  { id: "content", label: "🎬 Kontent", permission: "manage_movies" },
  { id: "collections", label: "🏷️ To'plamlar", permission: "manage_categories" },
  { id: "uploads", label: "📥 Yuklanganlar", permission: "manage_movies" },
  { id: "receipts", label: "💳 To'lovlar", permission: "manage_payments" },
  { id: "cards", label: "🎴 Kartalar", permission: "manage_payments" },
  { id: "promo", label: "🎟️ Promokod", permission: "manage_promo_codes" },
  { id: "users", label: "👥 Foydalanuvchilar", permission: "manage_users" },
  { id: "plans", label: "💎 Tariflar", permission: "manage_subscriptions" },
  { id: "admins", label: "🛡️ Adminlar", superAdminOnly: true },
];

interface Props {
  permissions: string[];
  isSuperAdmin: boolean;
}

export function AdminDashboard({ permissions, isSuperAdmin }: Props) {
  const visibleTabs = TABS.filter((item) =>
    item.superAdminOnly
      ? isSuperAdmin
      : isSuperAdmin || !item.permission || permissions.includes(item.permission),
  );

  // Falling back to the first tab they can actually open avoids landing an
  // admin without analytics on a panel that only 403s at them.
  const [tab, setTab] = useState<TabId>(visibleTabs[0]?.id ?? "stats");

  return (
    <div className="pb-24">
      <div className="sticky top-0 z-10 border-b border-surface-hi bg-bg/95 backdrop-blur">
        <div className="no-scrollbar flex gap-2 overflow-x-auto px-4 py-3">
          {visibleTabs.map((item) => (
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
        {tab === "collections" && <CollectionsPanel />}
        {tab === "uploads" && <UploadsPanel />}
        {tab === "receipts" && <ReceiptsPanel />}
        {tab === "cards" && <CardsPanel />}
        {tab === "promo" && <PromoPanel />}
        {tab === "users" && <UsersPanel />}
        {tab === "plans" && <PlansPanel />}
        {tab === "admins" && <AdminsPanel />}
      </div>
    </div>
  );
}
