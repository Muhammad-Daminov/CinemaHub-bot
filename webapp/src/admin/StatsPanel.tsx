/**
 * Dashboard overview: headline counters, a 7-day signup sparkline drawn
 * as inline SVG (no chart dependency), the catalog's type breakdown and
 * the top balance holders.
 */
import { useEffect, useState } from "react";
import { adminApi } from "../lib/api";
import type { ActivityPoint, AdminStats, TopUser } from "../types/admin";
import { CardShell, EmptyState, SectionTitle, contentTypeLabel, formatMoney } from "./ui";

const CHART_WIDTH = 300;
const CHART_HEIGHT = 90;
const CHART_PAD = 10;

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-surface-hi bg-surface p-3">
      <p className="font-mono text-lg font-semibold text-ink">{value}</p>
      <p className="mt-0.5 font-body text-[11px] leading-tight text-ink-dim">{label}</p>
    </div>
  );
}

function ActivityChart({ points }: { points: ActivityPoint[] }) {
  if (points.length === 0) return <EmptyState message="Ma'lumot yo'q." />;

  const max = Math.max(1, ...points.map((point) => point.count));
  const step =
    points.length > 1 ? (CHART_WIDTH - CHART_PAD * 2) / (points.length - 1) : 0;
  const baseline = CHART_HEIGHT - CHART_PAD;

  const coords = points.map((point, index) => ({
    x: CHART_PAD + index * step,
    y: baseline - (point.count / max) * (CHART_HEIGHT - CHART_PAD * 2),
    point,
  }));

  const line = coords.map((coord) => `${coord.x},${coord.y}`).join(" ");
  const area = `${CHART_PAD},${baseline} ${line} ${CHART_PAD + (points.length - 1) * step},${baseline}`;

  return (
    <div className="text-marquee">
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        className="h-24 w-full"
        role="img"
        aria-label="Oxirgi 7 kun ro'yxatdan o'tganlar"
      >
        <polygon points={area} fill="currentColor" opacity={0.15} />
        <polyline
          points={line}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        {coords.map((coord) => (
          <circle key={coord.point.date} cx={coord.x} cy={coord.y} r={2.5} fill="currentColor" />
        ))}
      </svg>
      <div className="mt-1 flex justify-between">
        {points.map((point) => (
          <span key={point.date} className="font-mono text-[10px] text-ink-dim">
            {point.date.slice(5)}
          </span>
        ))}
      </div>
    </div>
  );
}

export function StatsPanel() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [activity, setActivity] = useState<ActivityPoint[]>([]);
  const [topUsers, setTopUsers] = useState<TopUser[]>([]);

  useEffect(() => {
    adminApi.stats().then(setStats).catch(() => setStats(null));
    adminApi.activity().then(setActivity).catch(() => setActivity([]));
    adminApi.topUsers(5).then(setTopUsers).catch(() => setTopUsers([]));
  }, []);

  if (!stats) return <EmptyState message="Yuklanmoqda…" />;

  const byType = Object.entries(stats.titles_by_type);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <StatTile label="Foydalanuvchilar" value={formatMoney(stats.total_users)} />
        <StatTile label="Premium" value={formatMoney(stats.premium_users)} />
        <StatTile label="Kontent" value={formatMoney(stats.total_titles)} />
        <StatTile label="Qismlar" value={formatMoney(stats.total_episodes)} />
        <StatTile label="Kutayotgan to'lov" value={formatMoney(stats.pending_receipts)} />
        <StatTile label="Kutayotgan yuklama" value={formatMoney(stats.pending_uploads)} />
        <StatTile label="Tushum" value={formatMoney(stats.total_revenue)} />
        <StatTile label="Faol promokod" value={formatMoney(stats.active_promo_codes)} />
      </div>

      <section>
        <SectionTitle>Oxirgi 7 kun</SectionTitle>
        <CardShell>
          <ActivityChart points={activity} />
        </CardShell>
      </section>

      <section>
        <SectionTitle>Turlar bo'yicha</SectionTitle>
        <CardShell>
          {byType.length === 0 ? (
            <EmptyState message="Kontent yo'q." />
          ) : (
            <div className="space-y-2">
              {byType.map(([type, count]) => {
                const total = Math.max(1, stats.total_titles);
                return (
                  <div key={type}>
                    <div className="flex items-center justify-between">
                      <span className="font-body text-sm text-ink">{contentTypeLabel(type)}</span>
                      <span className="font-mono text-xs text-ink-dim">{count}</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-hi">
                      <div
                        className="h-full rounded-full bg-marquee"
                        style={{ width: `${(count / total) * 100}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardShell>
      </section>

      <section>
        <SectionTitle>Top foydalanuvchilar</SectionTitle>
        <CardShell>
          {topUsers.length === 0 ? (
            <EmptyState message="Foydalanuvchi yo'q." />
          ) : (
            <ul className="divide-y divide-surface-hi">
              {topUsers.map((user) => (
                <li key={user.telegram_id} className="flex items-center justify-between py-2">
                  <div className="min-w-0">
                    <p className="truncate font-body text-sm text-ink">
                      {user.username ? `@${user.username}` : "—"}
                    </p>
                    <p className="font-mono text-[11px] text-ink-dim">{user.telegram_id}</p>
                  </div>
                  <span className="shrink-0 font-mono text-sm text-marquee">
                    {formatMoney(user.balance)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardShell>
      </section>
    </div>
  );
}
