/**
 * The broadcast control centre: compose, watch, recover, review.
 *
 * Three screens behind one panel — the composer, a progress view for one
 * broadcast, and the history — because a broadcast is not a form
 * submission. It is a job that runs for minutes and can fail halfway, and
 * an operator needs to see it doing that.
 *
 * **Every number here is the server's.** Progress comes from
 * `GET /admin/broadcasts/{id}`, which counts the per-recipient rows; it is
 * never derived from the pre-send estimate, because an estimate is a guess
 * about the future while those rows are what actually happened. The Resume
 * button appears only when the server says `can_resume` — the panel may
 * have been open for an hour, so it is in no position to judge.
 *
 * Polling is a self-cancelling chain of timeouts rather than an interval:
 * an interval survives a re-render and accumulates, and two loops polling
 * the same broadcast is a request storm with no upper bound. It stops on a
 * terminal status, on unmount, and while the tab is hidden.
 *
 * Nothing here can name a recipient, and no response it reads carries a
 * `file_id` — the API returns the media *kind*, never the reference.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { adminApi } from "../lib/api";
import type {
  AdminBroadcast,
  AdminBroadcastDetail,
  BroadcastStatus,
} from "../types/admin";
import {
  AUDIENCES,
  BroadcastComposer,
  MEDIA_TYPES,
  describeError,
  useTargetLabel,
} from "./BroadcastComposer";
import { Button, EmptyState, Notice, SectionTitle } from "./ui";

/**
 * Backend status → operator-facing label, in one place.
 *
 * Only the four statuses the backend actually has. A UI-only status would
 * be a claim about the system that nothing in the system supports.
 */
const STATUS_LABELS: Record<BroadcastStatus, string> = {
  pending: "Navbatda",
  sending: "Yuborilmoqda",
  completed: "Yakunlandi",
  failed: "Xatolik",
};

/** Status → a shape, so state is never carried by colour alone. */
const STATUS_MARKS: Record<BroadcastStatus, string> = {
  pending: "◷",
  sending: "▸",
  completed: "✓",
  failed: "✕",
};

const POLL_INTERVAL_MS = 4000;

function isRunning(status: BroadcastStatus): boolean {
  return status === "pending" || status === "sending";
}

export function BroadcastPanel() {
  const [history, setHistory] = useState<AdminBroadcast[] | null>(null);
  const [watching, setWatching] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await adminApi.listBroadcasts());
      setError(null);
    } catch (err) {
      setHistory([]);
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  if (watching !== null) {
    return (
      <BroadcastProgress
        broadcastId={watching}
        onClose={() => {
          setWatching(null);
          loadHistory();
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      {error && <Notice message={error} tone="error" />}

      <BroadcastComposer
        onSent={(created) => {
          setWatching(created.id);
          loadHistory();
        }}
      />

      <div>
        <SectionTitle>Tarix</SectionTitle>
        {history === null ? (
          <p className="font-body text-sm text-ink-dim">Yuklanmoqda…</p>
        ) : history.length === 0 ? (
          <EmptyState message="Hali xabar yuborilmagan." />
        ) : (
          <ul className="space-y-2">
            {history.map((row) => (
              <HistoryRow key={row.id} row={row} onOpen={() => setWatching(row.id)} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function HistoryRow({ row, onOpen }: { row: AdminBroadcast; onOpen: () => void }) {
  const targetLabel = useTargetLabel();
  const audience = AUDIENCES.find((item) => item.value === row.audience);
  const media = MEDIA_TYPES.find((item) => item.value === row.media_type);

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="w-full rounded-xl border border-surface-hi bg-surface p-3 text-left"
      >
        <div className="flex items-start justify-between gap-2">
          {/* The admin's own text, rendered as text. */}
          <p className="min-w-0 flex-1 font-body text-sm text-ink line-clamp-2">{row.message}</p>
          <span className="shrink-0 font-mono text-[11px] text-ink-dim">
            {STATUS_MARKS[row.status]} {STATUS_LABELS[row.status] ?? row.status}
          </span>
        </div>
        <p className="mt-1 font-mono text-[11px] text-ink-dim">
          {audience?.label ?? row.audience}
          {row.target_value && ` → ${targetLabel(row.audience, row.target_value)}`}
          {row.media_type !== "none" && ` · ${media?.label}`}
          {" · "}
          {row.sent_count}/{row.total_recipients} yuborildi
          {row.blocked_count > 0 && ` · ${row.blocked_count} bloklagan`}
          {row.failed_count > 0 && ` · ${row.failed_count} xato`}
        </p>
      </button>
    </li>
  );
}

/**
 * Live progress for one broadcast, with recovery when the server offers it.
 */
function BroadcastProgress({
  broadcastId,
  onClose,
}: {
  broadcastId: number;
  onClose: () => void;
}) {
  const targetLabel = useTargetLabel();
  const [detail, setDetail] = useState<AdminBroadcastDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Synchronous, so a double tap cannot start two resumes. */
  const resuming = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const value = await adminApi.broadcastDetail(broadcastId);
      setDetail(value);
      setError(null);
      return value;
    } catch (err) {
      // A failed poll must not blank a screen that was already working —
      // the previous state stays, and the message explains the gap.
      setError(describeError(err));
      return null;
    }
  }, [broadcastId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  /**
   * One timeout at a time, re-armed after each successful poll. `cancelled`
   * makes the in-flight request harmless after unmount, and the hidden-tab
   * check stops a backgrounded Mini App polling forever.
   */
  useEffect(() => {
    if (detail === null || !isRunning(detail.status)) return;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      if (typeof document !== "undefined" && document.hidden) {
        // Re-arm cheaply rather than fetching: the effect re-runs when the
        // next poll lands, and a hidden tab has nobody to show it to.
        setDetail((current) => (current ? { ...current } : current));
        return;
      }
      refresh();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [detail, refresh]);

  const resume = async () => {
    if (resuming.current) return;
    resuming.current = true;
    setBusy(true);
    try {
      await adminApi.resumeBroadcast(broadcastId);
      setError(null);
    } catch (err) {
      // 409 means the state moved under us — refreshing is the answer,
      // retrying is not.
      setError(describeError(err));
    } finally {
      await refresh();
      resuming.current = false;
      setBusy(false);
    }
  };

  if (detail === null) {
    return (
      <div className="space-y-3">
        <SectionTitle>Xabar holati</SectionTitle>
        {error ? <Notice message={error} tone="error" /> : <p className="font-body text-sm text-ink-dim">Yuklanmoqda…</p>}
        <Button full tone="ghost" onClick={onClose}>
          Orqaga
        </Button>
      </div>
    );
  }

  const audience = AUDIENCES.find((item) => item.value === detail.audience);
  const media = MEDIA_TYPES.find((item) => item.value === detail.media_type);
  const done = detail.sent + detail.failed + detail.skipped;
  const percent =
    detail.total_recipients > 0
      ? Math.min(100, Math.round((done / detail.total_recipients) * 100))
      : 0;

  return (
    <div className="space-y-3">
      <SectionTitle>Xabar holati</SectionTitle>
      {error && <Notice message={error} tone="error" />}

      <div className="space-y-2 rounded-xl border border-surface-hi bg-surface p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="font-body text-sm font-semibold text-ink">
            {STATUS_MARKS[detail.status]} {STATUS_LABELS[detail.status] ?? detail.status}
          </span>
          <span className="font-mono text-xs text-ink-dim">
            {audience?.label ?? detail.audience}
            {detail.target_value && ` → ${targetLabel(detail.audience, detail.target_value)}`}
          </span>
        </div>

        <div
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Yuborish jarayoni"
          className="h-2 w-full overflow-hidden rounded-full bg-surface-hi"
        >
          <div
            className="h-full rounded-full bg-marquee transition-[width] duration-500"
            style={{ width: `${percent}%` }}
          />
        </div>

        <p className="font-mono text-xs text-ink-dim">
          {done.toLocaleString("ru-RU")} / {detail.total_recipients.toLocaleString("ru-RU")} ({percent}%)
        </p>

        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[11px] text-ink-dim">
          <Stat label="Yuborildi" value={detail.sent} />
          <Stat label="Navbatda" value={detail.pending} />
          <Stat label="Bloklagan" value={detail.skipped} />
          <Stat label="Xato" value={detail.failed} />
        </dl>

        <p className="font-mono text-[11px] text-ink-dim">
          {media?.label}
          {detail.languages.length > 0 && ` · ${detail.languages.join(" / ").toUpperCase()}`}
        </p>

        {detail.error && <Notice message={detail.error} tone="error" />}
      </div>

      {detail.can_resume && (
        <div className="space-y-2 rounded-xl border border-surface-hi bg-surface p-3">
          <p className="font-body text-sm text-ink">
            Bu xabar to'liq yuborilmagan. Davom ettirish mumkin.
          </p>
          <p className="font-body text-[11px] text-ink-dim">
            Faqat qolganlarga yuboriladi — ro'yxat o'zgarmaydi.
          </p>
          <Button full tone="danger" disabled={busy} onClick={resume}>
            {busy ? "Davom ettirilmoqda…" : "Davom ettirish"}
          </Button>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <Button tone="ghost" disabled={busy} onClick={refresh}>
          <RefreshCw size={14} aria-hidden /> Yangilash
        </Button>
        <Button tone="ghost" onClick={onClose}>
          Orqaga
        </Button>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex justify-between gap-2">
      <dt>{label}</dt>
      <dd className="font-medium text-ink">{value.toLocaleString("ru-RU")}</dd>
    </div>
  );
}
