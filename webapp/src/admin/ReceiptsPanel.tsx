/**
 * Payment receipt review. Approve/reject delegate to the same backend
 * service the bot's inline buttons use, so the two paths can't drift.
 *
 * The photo modal streams the image through GET /admin/receipts/{id}/photo
 * rather than linking Telegram directly — Telegram's file URLs embed the
 * bot token, which must never reach the client.
 */
import { Check, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import { ZoomIn, ZoomOut } from "lucide-react";
import { getInitData } from "../lib/telegram";
import type { AdminReceipt } from "../types/admin";
import { Badge, Button, EmptyState, Notice, SectionTitle, TextInput, formatMoney } from "./ui";

const ZOOM_STEPS = [1, 1.75, 3];

function PhotoModal({ receipt, onClose }: { receipt: AdminReceipt; onClose: () => void }) {
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(0);

  // A plain <img src> wouldn't carry X-Telegram-Init-Data, and the route is
  // admin-gated — so fetch the bytes with the header, then hand the <img> an
  // object URL. Revoked on unmount so the blob isn't leaked.
  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;

    (async () => {
      try {
        // Two sources: receipts uploaded in the Mini App have bytes of our
        // own, older ones are proxied from Telegram. Try ours first and
        // fall back, so both eras of receipt open in the same viewer.
        const headers = { "X-Telegram-Init-Data": getInitData() };
        let response = await fetch(`/api/admin/receipts/${receipt.id}/image`, { headers });
        if (response.status === 404) {
          response = await fetch(`/api/admin/receipts/${receipt.id}/photo`, { headers });
        }
        if (!response.ok) {
          const body = await response.json().catch(() => ({ detail: response.statusText }));
          throw new Error(body.detail ?? "Rasmni yuklab bo'lmadi.");
        }
        const blob = await response.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPhotoUrl(objectUrl);
      } catch (err) {
        if (!cancelled) {
          setPhotoError(err instanceof Error ? err.message : "Rasmni yuklab bo'lmadi.");
        }
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [receipt.id]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-bg/90 p-6 backdrop-blur"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl border border-surface-hi bg-surface p-4"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <p className="font-display text-sm font-medium text-ink">Chek rasmi</p>
          {photoUrl && (
            <div className="flex gap-1">
              <Button
                tone="ghost"
                disabled={zoom === 0}
                onClick={() => setZoom((z) => Math.max(z - 1, 0))}
              >
                <ZoomOut size={14} />
              </Button>
              <Button
                tone="ghost"
                disabled={zoom === ZOOM_STEPS.length - 1}
                onClick={() => setZoom((z) => Math.min(z + 1, ZOOM_STEPS.length - 1))}
              >
                <ZoomIn size={14} />
              </Button>
            </div>
          )}
        </div>
        {/* Scrolls once zoomed, so the admin can pan to the amount rather
            than squinting at a whole receipt scaled to a phone screen. */}
        <div className="mt-3 flex aspect-[3/4] items-center justify-center overflow-auto rounded-lg bg-surface-hi">
          {photoUrl ? (
            <img
              src={photoUrl}
              alt="To'lov cheki"
              onClick={() => setZoom((z) => (z + 1) % ZOOM_STEPS.length)}
              style={{ transform: `scale(${ZOOM_STEPS[zoom]})` }}
              className="h-full w-full origin-center cursor-zoom-in object-contain transition-transform"
            />
          ) : (
            <p className="px-4 text-center font-body text-xs text-ink-dim">
              {photoError ? "Rasm yuklanmadi." : "Yuklanmoqda…"}
            </p>
          )}
        </div>
        {photoError && (
          <div className="mt-3">
            <Notice message={photoError} tone="error" />
          </div>
        )}
        <div className="mt-3">
          <Button full tone="ghost" onClick={onClose}>
            Yopish
          </Button>
        </div>
      </div>
    </div>
  );
}

export function ReceiptsPanel() {
  const [receipts, setReceipts] = useState<AdminReceipt[]>([]);
  const [photo, setPhoto] = useState<AdminReceipt | null>(null);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReceipts(await adminApi.listReceipts("pending"));
    } catch {
      setReceipts([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleApprove = async (id: number) => {
    try {
      await adminApi.approveReceipt(id);
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Tasdiqlashda xatolik.");
    }
  };

  const handleReject = async (id: number) => {
    if (!reason.trim()) {
      setError("Rad etish sababini kiriting.");
      return;
    }
    try {
      await adminApi.rejectReceipt(id, reason.trim());
      setRejectingId(null);
      setReason("");
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Rad etishda xatolik.");
    }
  };

  return (
    <div className="space-y-3">
      <SectionTitle>Kutayotgan to'lovlar</SectionTitle>
      {error && <Notice message={error} tone="error" />}

      {receipts.length === 0 ? (
        <EmptyState message="Kutayotgan to'lov yo'q." />
      ) : (
        <ul className="space-y-2">
          {receipts.map((receipt) => (
            <li key={receipt.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <button onClick={() => setPhoto(receipt)} className="w-full text-left">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-body text-sm text-ink">
                      {receipt.full_name ?? receipt.username ?? "—"}
                    </p>
                    <p className="font-mono text-[11px] text-ink-dim">{receipt.telegram_id}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="font-mono text-sm text-marquee">{formatMoney(receipt.amount)}</p>
                    <Badge>{receipt.purpose}</Badge>
                  </div>
                </div>
              </button>

              {rejectingId === receipt.id ? (
                <div className="mt-2 space-y-2">
                  <TextInput value={reason} onChange={setReason} placeholder="Rad etish sababi…" />
                  <div className="grid grid-cols-2 gap-2">
                    <Button tone="danger" onClick={() => handleReject(receipt.id)}>
                      Rad etish
                    </Button>
                    <Button
                      tone="ghost"
                      onClick={() => {
                        setRejectingId(null);
                        setReason("");
                      }}
                    >
                      Bekor qilish
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <Button onClick={() => handleApprove(receipt.id)}>
                    <span className="inline-flex items-center justify-center gap-1.5">
                      <Check size={15} /> Tasdiqlash
                    </span>
                  </Button>
                  <Button tone="ghost" onClick={() => setRejectingId(receipt.id)}>
                    <span className="inline-flex items-center justify-center gap-1.5">
                      <X size={15} /> Rad etish
                    </span>
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {photo && <PhotoModal receipt={photo} onClose={() => setPhoto(null)} />}
    </div>
  );
}
