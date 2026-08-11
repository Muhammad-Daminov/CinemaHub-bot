/**
 * Admin panel shell.
 *
 * Navigation is a two-level menu: five groups, each holding the sections
 * that belong to it. It replaced a single horizontal strip of thirteen
 * pills, which on a phone meant scrolling sideways through tiny targets
 * to find anything — the panel is used from Telegram, so a phone is the
 * normal case, not the degraded one.
 *
 * On a phone the menu and the open section are separate screens: pick a
 * group, pick a section, work, then Back. One thumb, full-width rows,
 * nothing to scroll horizontally. From `md` up both are on screen at
 * once as a sidebar plus panel, because there the width exists and
 * making an admin tap twice to change section would be a regression.
 *
 * Tab state stays plain useState — the project has no router, and the
 * panel is a leaf of the Mini App rather than a separate destination, so
 * there is still nothing to keep in the URL.
 *
 * This file is presentation only. Every panel, API call and permission
 * check below is untouched: the grouping is a rearrangement of the same
 * thirteen sections, and a hidden group is a courtesy, never a boundary.
 */
import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { CardsPanel } from "./CardsPanel";
import { CollectionsPanel } from "./CollectionsPanel";
import { ContentPanel } from "./ContentPanel";
import { PlansPanel } from "./PlansPanel";
import { PromoPanel } from "./PromoPanel";
import { ReceiptsPanel } from "./ReceiptsPanel";
import { StatsPanel } from "./StatsPanel";
import { UploadsPanel } from "./UploadsPanel";
import { AdminsPanel } from "./AdminsPanel";
import { AppearancePanel } from "./AppearancePanel";
import { BroadcastPanel } from "./BroadcastPanel";
import { SettingsPanel } from "./SettingsPanel";
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
  | "broadcast"
  | "appearance"
  | "settings"
  | "admins";

type GroupId = "content" | "people" | "finance" | "analytics" | "system";

/**
 * Each tab names the permission that backs it. The API enforces the same
 * permission on every route the tab calls, so hiding it is presentation
 * only — a hidden tab is a courtesy, not the security boundary.
 *
 * `superAdminOnly` marks the administrator management tab: appointing
 * admins is not a grantable capability, so it cannot be expressed as one.
 *
 * `group` is the only field added by the navigation rework. Labels,
 * permissions and ids are unchanged, so an administrator looks for the
 * same words they already know — just one level in.
 */
const TABS: {
  id: TabId;
  label: string;
  group: GroupId;
  permission?: string;
  superAdminOnly?: boolean;
}[] = [
  { id: "stats", label: "📊 Statistika", group: "analytics", permission: "view_analytics" },
  { id: "content", label: "🎬 Kontent", group: "content", permission: "manage_movies" },
  { id: "collections", label: "🏷️ To'plamlar", group: "content", permission: "manage_categories" },
  { id: "uploads", label: "📥 Yuklanganlar", group: "content", permission: "manage_movies" },
  { id: "receipts", label: "💳 To'lovlar", group: "finance", permission: "manage_payments" },
  { id: "cards", label: "🎴 Kartalar", group: "finance", permission: "manage_payments" },
  { id: "promo", label: "🎟️ Promokod", group: "finance", permission: "manage_promo_codes" },
  { id: "users", label: "👥 Foydalanuvchilar", group: "people", permission: "manage_users" },
  { id: "plans", label: "💎 Tariflar", group: "finance", permission: "manage_subscriptions" },
  { id: "broadcast", label: "📣 Xabarlar", group: "system", permission: "manage_notifications" },
  // Themes, assignments and banners in one section rather than scattered
  // across the panel. Gated on manage_system_settings, the same capability
  // the theme API requires — the banner tab's own calls are additionally
  // gated on manage_notifications by the backend.
  { id: "appearance", label: "🎨 Ko'rinish", group: "system", permission: "manage_system_settings" },
  { id: "settings", label: "⚙️ Sozlamalar", group: "system", permission: "manage_system_settings" },
  { id: "admins", label: "🛡️ Adminlar", group: "people", superAdminOnly: true },
];

// Order here is the order on screen: the things an operator opens daily
// first, the ones they configure once last.
const GROUPS: { id: GroupId; label: string; hint: string }[] = [
  { id: "content", label: "🎬 Kontent", hint: "Kinolar, to'plamlar, yuklanganlar" },
  { id: "finance", label: "💰 Moliya", hint: "To'lovlar, kartalar, tariflar, promokod" },
  { id: "people", label: "👥 Odamlar", hint: "Foydalanuvchilar va adminlar" },
  { id: "analytics", label: "📊 Tahlil", hint: "Statistika" },
  { id: "system", label: "⚙️ Tizim", hint: "Xabarlar, ko'rinish, sozlamalar" },
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

  // A group with nothing visible in it is dropped entirely rather than
  // shown empty — an administrator without payment permissions should not
  // be offered a Finance group that opens onto nothing.
  const visibleGroups = GROUPS.map((group) => ({
    ...group,
    sections: visibleTabs.filter((item) => item.group === group.id),
  })).filter((group) => group.sections.length > 0);

  // Falling back to the first section they can actually open avoids landing
  // an admin without analytics on a panel that only 403s at them.
  const firstTab = visibleTabs[0]?.id ?? "stats";
  const [tab, setTab] = useState<TabId>(firstTab);
  // Which group is expanded in the menu. Starts on the one holding the
  // default section, so the menu opens showing where the operator already is.
  const [openGroup, setOpenGroup] = useState<GroupId | null>(
    visibleTabs[0]?.group ?? null,
  );
  // Phone only: the menu and the panel are separate screens. True means the
  // menu is showing. Ignored from `md` up, where both are always visible.
  const [menuOnTop, setMenuOnTop] = useState(true);

  const activeTab = visibleTabs.find((item) => item.id === tab);
  const activeGroup = GROUPS.find((group) => group.id === activeTab?.group);

  const openSection = (id: TabId) => {
    setTab(id);
    setMenuOnTop(false);
  };

  const menu = (
    <nav className="space-y-2">
      {visibleGroups.map((group) => {
        const expanded = openGroup === group.id;
        return (
          <div
            key={group.id}
            className="overflow-hidden rounded-xl border border-surface-hi bg-surface"
          >
            <button
              onClick={() => setOpenGroup(expanded ? null : group.id)}
              aria-expanded={expanded}
              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
            >
              <span className="min-w-0">
                <span className="block font-display text-sm font-medium text-ink">
                  {group.label}
                </span>
                <span className="mt-0.5 block truncate font-body text-[11px] text-ink-dim">
                  {group.hint}
                </span>
              </span>
              <ChevronRight
                size={16}
                className={`shrink-0 text-ink-dim transition-transform ${
                  expanded ? "rotate-90" : ""
                }`}
              />
            </button>

            {expanded && (
              <ul className="border-t border-surface-hi">
                {group.sections.map((section) => (
                  <li key={section.id}>
                    <button
                      onClick={() => openSection(section.id)}
                      aria-current={tab === section.id ? "page" : undefined}
                      // Full width and a generous hit area: this is the row
                      // a thumb actually lands on.
                      className={`w-full px-4 py-3 text-left font-body text-sm transition-colors ${
                        tab === section.id
                          ? "bg-marquee text-on-marquee"
                          : "text-ink-dim hover:text-ink"
                      }`}
                    >
                      {section.label}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </nav>
  );

  return (
    <div className="pb-24 md:grid md:grid-cols-[240px_1fr] md:items-start md:gap-4 md:p-4">
      {/* Phone: one screen at a time. Desktop: a sticky sidebar. */}
      <div className={`p-4 md:sticky md:top-4 md:block md:p-0 ${menuOnTop ? "" : "hidden"}`}>
        {menu}
      </div>

      <div className={`md:block ${menuOnTop ? "hidden" : ""}`}>
        {/* Back is phone-only — on desktop the menu never went away. */}
        <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-surface-hi bg-bg/95 px-4 py-3 backdrop-blur md:hidden">
          <button
            onClick={() => setMenuOnTop(true)}
            aria-label="Orqaga"
            className="flex items-center gap-1 rounded-full border border-surface-hi bg-surface px-3 py-1.5 font-body text-xs text-ink-dim"
          >
            <ChevronLeft size={14} /> Menyu
          </button>
          <span className="min-w-0 truncate font-display text-sm font-medium text-ink">
            {activeTab?.label}
          </span>
        </div>

        {/* Desktop keeps the same orientation cue without the back control. */}
        <p className="hidden px-1 pb-3 font-body text-[11px] text-ink-dim md:block">
          {activeGroup?.label} › <span className="text-ink">{activeTab?.label}</span>
        </p>

        <div className="p-4 md:p-0">
          {tab === "stats" && <StatsPanel />}
          {tab === "content" && <ContentPanel />}
          {tab === "collections" && <CollectionsPanel />}
          {tab === "uploads" && <UploadsPanel />}
          {tab === "receipts" && <ReceiptsPanel />}
          {tab === "cards" && <CardsPanel />}
          {tab === "promo" && <PromoPanel />}
          {tab === "users" && <UsersPanel />}
          {tab === "plans" && <PlansPanel />}
          {tab === "broadcast" && <BroadcastPanel />}
          {tab === "appearance" && <AppearancePanel />}
          {tab === "settings" && <SettingsPanel />}
          {tab === "admins" && <AdminsPanel />}
        </div>
      </div>
    </div>
  );
}
