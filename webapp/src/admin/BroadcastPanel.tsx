/**
 * Admin broadcasts: compose, pick an audience, send, then watch the
 * delivery counts.
 *
 * Two deliberate frictions, because this is the one control in the panel
 * that cannot be undone:
 *
 *  - The audience size is shown next to each option *before* sending, so
 *    "everyone" is a number rather than a word.
 *  - Sending asks for confirmation and the button locks while the request
 *    is in flight. The backend refuses a duplicate anyway, but a button
 *    that stays live invites the second click that made the guard
 *    necessary.
 *
 * Progress is polled while a broadcast is SENDING and stops when it is
 * not — several thousand messages take minutes, and a static screen
 * looks like a broadcast that never started.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type { AdminBroadcast, BroadcastAudience, BroadcastAudienceSize } from "../types/admin";
import { Button, EmptyState, Field, Notice, SectionTitle, TextArea } from "./ui";

const AUDIENCE_LABELS: Record<BroadcastAudience, string> = {
  all: "Hammaga",
  premium: "Obunachilarga",
  free: "Obunasizlarga",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "Navbatda",
  sending: "Yuborilmoqda",
  completed: "Yakunlandi",
  failed: "Xatolik",
};

const POLL_INTERVAL_MS = 4000;

export function BroadcastPanel() {
  const [message, setMessage] = useState("");
  const [audience, setAudience] = useState<BroadcastAudience>("all");
  const [sizes, setSizes] = useState<BroadcastAudienceSize[]>([]);
  const [history, setHistory] = useState<AdminBroadcast[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [rows, audiences] = await Promise.all([
        adminApi.listBroadcasts(),
        adminApi.broadcastAudiences(),
      ]);
      setHistory(rows);
      setSizes(audiences);
    } catch {
      setHistory([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Polls only while something is actually in flight.
  useEffect(() => {
    const running = history.some((row) => row.status === "sending" || row.status === "pending");
    if (!running) return;
    timer.current = window.setTimeout(load, POLL_INTERVAL_MS);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [history, load]);

  const audienceSize = (value: BroadcastAudience) =>
    sizes.find((item) => item.audience === value)?.size ?? 0;

  const send = async () => {
    setBusy(true);
    try {
      await adminApi.sendBroadcast(message.trim(), audience);
      setMessage("");
      setConfirming(false);
      setError(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yuborishda xatolik.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <SectionTitle>Xabar yuborish</SectionTitle>
      {error && <Notice message={error} tone="error" />}

      <Field label="Xabar matni">
        <TextArea value={message} onChange={setMessage} rows={5} placeholder="Xabar…" />
      </Field>

      <div className="space-y-1.5">
        {(Object.keys(AUDIENCE_LABELS) as BroadcastAudience[]).map((value) => (
          <button
            key={value}
            onClick={() => setAudience(value)}
            className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left font-body text-sm transition-colors ${
              audience === value
                ? "border-marquee bg-surface-hi text-ink"
                : "border-surface-hi bg-surface text-ink-dim"
            }`}
          >
            <span>{AUDIENCE_LABELS[value]}</span>
            <span className="font-mono text-xs">{audienceSize(value)}</span>
          </button>
        ))}
      </div>

      {confirming ? (
        <div className="space-y-2 rounded-xl border border-surface-hi bg-surface p-3">
          <p className="font-body text-sm text-ink">
            {audienceSize(audience)} ta foydalanuvchiga yuborilsinmi? Bekor qilib bo'lmaydi.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <Button tone="danger" disabled={busy} onClick={send}>
              Yuborish
            </Button>
            <Button tone="ghost" disabled={busy} onClick={() => setConfirming(false)}>
              Bekor qilish
            </Button>
          </div>
        </div>
      ) : (
        <Button full disabled={!message.trim()} onClick={() => setConfirming(true)}>
          Yuborish
        </Button>
      )}

      <SectionTitle>Tarix</SectionTitle>
      {history.length === 0 ? (
        <EmptyState message="Hali xabar yuborilmagan." />
      ) : (
        <ul className="space-y-2">
          {history.map((row) => (
            <li key={row.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 flex-1 font-body text-sm text-ink line-clamp-2">
                  {row.message}
                </p>
                <span className="shrink-0 font-mono text-[11px] text-ink-dim">
                  {STATUS_LABELS[row.status] ?? row.status}
                </span>
              </div>
              <p className="mt-1 font-mono text-[11px] text-ink-dim">
                {AUDIENCE_LABELS[row.audience]} · {row.sent_count}/{row.total_recipients} yuborildi
                {row.blocked_count > 0 && ` · ${row.blocked_count} bloklagan`}
                {row.failed_count > 0 && ` · ${row.failed_count} xato`}
              </p>
              {row.error && <Notice message={row.error} tone="error" />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
