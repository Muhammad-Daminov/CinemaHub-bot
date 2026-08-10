/**
 * Payment receipt review. Approve/reject delegate to the same backend
 * service the bot's inline buttons use, so the two paths can't drift.
 *
 * The photo modal streams the image through GET /admin/receipts/{id}/photo
 * rather than linking Telegram directly — Telegram's file URLs embed the
 * bot token, which must never reach the client.
 */
import { AlertTriangle, Check, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import { ZoomIn, ZoomOut } from "lucide-react";
import { getInitData } from "../lib/telegram";
import type { AdminReceipt, AdminRejectionReason, PaymentStatus } from "../types/admin";
import { Badge, Button, EmptyState, Notice, SectionTitle, TextInput, formatMoney } from "./ui";

const ZOOM_STEPS = [1, 1.75, 3];

// Panel-side labels for the built-in reason codes. The *user* sees these
// through the locale catalogs (payment.reject.<code>) — this panel is
// hardcoded Uzbek like the rest of the admin UI (TASKS.md P2-9).
const REASON_LABELS: Record<string, string> = {
  incorrect_amount: "Summa noto'g'ri",
  suspicious_receipt: "Chek shubhali",
  unreadable_receipt: "Chek o'qib bo'lmaydi",
  unverifiable: "Tasdiqlab bo'lmadi",
  wrong_destination: "Noto'g'ri karta",
  duplicate_payment: "Takroriy to'lov",
  other: "Boshqa",
};

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

const STATUS_LABELS: Record<string, string> = {
  pending: "Kutilmoqda",
  approved: "Tasdiqlangan",
  rejected: "Rad etilgan",
  mismatch: "Summa mos kelmadi",
  cancelled: "Bekor qilingan",
};

const FILTERS: { id: PaymentStatus | "all"; label: string }[] = [
  { id: "pending", label: "Kutilmoqda" },
  { id: "approved", label: "Tasdiqlangan" },
  { id: "mismatch", label: "Summa" },
  { id: "rejected", label: "Rad etilgan" },
  { id: "all", label: "Hammasi" },
];

export function ReceiptsPanel() {
  const [receipts, setReceipts] = useState<AdminReceipt[]>([]);
  const [photo, setPhoto] = useState<AdminReceipt | null>(null);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<PaymentStatus | "all">("pending");
  const [reasons, setReasons] = useState<AdminRejectionReason[]>([]);
  const [reasonId, setReasonId] = useState<number | null>(null);
  const [mismatchId, setMismatchId] = useState<number | null>(null);
  const [verifiedAmount, setVerifiedAmount] = useState("");
  const [query, setQuery] = useState("");

  // Searched server-side: filtering the loaded list would only ever search
  // the most recent page, and the receipt an admin is looking for is
  // usually the one that has scrolled off it.
  const load = useCallback(async () => {
    try {
      setReceipts(
        await adminApi.listReceipts({
          status: filter === "all" ? undefined : filter,
          q: query.trim() || undefined,
        }),
      );
    } catch {
      setReceipts([]);
    }
  }, [filter, query]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    adminApi.rejectionReasons().then(setReasons).catch(() => setReasons([]));
  }, []);

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
    if (reasonId === null && !reason.trim()) {
      setError("Sabab tanlang yoki yozing.");
      return;
    }
    try {
      await adminApi.rejectReceipt(id, reason.trim() || null, reasonId);
      setRejectingId(null);
      setReasonId(null);
      setReason("");
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Rad etishda xatolik.");
    }
  };

  const handleMismatch = async (id: number) => {
    const actual = Number(verifiedAmount);
    if (!actual || actual <= 0) {
      setError("Chekdagi aniq summani kiriting.");
      return;
    }
    try {
      await adminApi.flagMismatch(id, actual);
      setMismatchId(null);
      setVerifiedAmount("");
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Saqlashda xatolik.");
    }
  };

  /** Built-ins are localized by the backend; admin-authored ones show as typed. */
  const reasonLabel = (item: AdminRejectionReason) =>
    item.label ?? REASON_LABELS[item.code ?? ""] ?? item.code ?? "—";

  return (
    <div className="space-y-3">
      <SectionTitle>To'lovlar</SectionTitle>
      {error && <Notice message={error} tone="error" />}

      <div className="no-scrollbar flex gap-2 overflow-x-auto">
        {FILTERS.map((item) => (
          <button
            key={item.id}
            onClick={() => setFilter(item.id)}
            className={`shrink-0 rounded-full px-3 py-1.5 font-body text-xs transition-colors ${
              filter === item.id
                ? "bg-marquee text-on-marquee"
                : "border border-surface-hi bg-surface text-ink-dim"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <TextInput value={query} onChange={setQuery} placeholder="Ism, username yoki ID…" />

      {receipts.length === 0 ? (
        <EmptyState message="To'lov topilmadi." />
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

              {/* Approve/reject only apply to a receipt still awaiting
                  review — the history views are read-only. */}
              {/* Audit trail — visible on every receipt, decided or not. */}
              <p className="mt-1 font-mono text-[10px] leading-relaxed text-ink-dim">
                {new Date(receipt.created_at).toLocaleString()}
                {receipt.card_label ? ` · ${receipt.card_label}` : ""}
                {receipt.reviewed_at
                  ? ` · ko'rib chiqildi ${new Date(receipt.reviewed_at).toLocaleString()}`
                  : ""}
                {receipt.reviewer_telegram_id ? ` · admin ${receipt.reviewer_telegram_id}` : ""}
              </p>

              {receipt.status !== "pending" ? (
                <p className="mt-2 font-mono text-[11px] text-ink-dim">
                  {STATUS_LABELS[receipt.status] ?? receipt.status}
                  {receipt.verified_amount != null
                    ? ` · chekda ${formatMoney(receipt.verified_amount)}`
                    : ""}
                  {receipt.admin_notes ? ` · ${receipt.admin_notes}` : ""}
                </p>
              ) : mismatchId === receipt.id ? (
                <div className="mt-2 space-y-2">
                  <p className="font-body text-xs text-ink-dim">
                    Chekdagi haqiqiy summani kiriting. Hech qanday balans qo'shilmaydi —
                    foydalanuvchi ikkala summani ko'rib, qayta yuboradi.
                  </p>
                  <TextInput
                    value={verifiedAmount}
                    onChange={(value) => setVerifiedAmount(value.replace(/[^0-9]/g, ""))}
                    placeholder="Chekdagi summa"
                    mono
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <Button tone="danger" onClick={() => handleMismatch(receipt.id)}>
                      Saqlash
                    </Button>
                    <Button
                      tone="ghost"
                      onClick={() => {
                        setMismatchId(null);
                        setVerifiedAmount("");
                      }}
                    >
                      Bekor qilish
                    </Button>
                  </div>
                </div>
              ) : rejectingId === receipt.id ? (
                <div className="mt-2 space-y-2">
                  {/* Predefined reasons are localized for the user by the
                      backend; the free-text box is for anything else. */}
                  <div className="flex flex-wrap gap-1.5">
                    {reasons.map((item) => (
                      <button
                        key={item.id}
                        onClick={() => setReasonId(reasonId === item.id ? null : item.id)}
                        className={`rounded-full px-2.5 py-1 font-body text-[11px] transition-colors ${
                          reasonId === item.id
                            ? "bg-marquee text-on-marquee"
                            : "border border-surface-hi bg-surface text-ink-dim"
                        }`}
                      >
                        {reasonLabel(item)}
                      </button>
                    ))}
                  </div>
                  <TextInput value={reason} onChange={setReason} placeholder="Qo'shimcha izoh…" />
                  <div className="grid grid-cols-2 gap-2">
                    <Button tone="danger" onClick={() => handleReject(receipt.id)}>
                      Rad etish
                    </Button>
                    <Button
                      tone="ghost"
                      onClick={() => {
                        setRejectingId(null);
                        setReasonId(null);
                        setReason("");
                      }}
                    >
                      Bekor qilish
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-2 grid grid-cols-3 gap-2">
                  <Button onClick={() => handleApprove(receipt.id)}>
                    <span className="inline-flex items-center justify-center gap-1.5">
                      <Check size={15} /> Tasdiq
                    </span>
                  </Button>
                  <Button tone="ghost" onClick={() => setMismatchId(receipt.id)}>
                    <span className="inline-flex items-center justify-center gap-1.5">
                      <AlertTriangle size={15} /> Summa
                    </span>
                  </Button>
                  <Button tone="ghost" onClick={() => setRejectingId(receipt.id)}>
                    <span className="inline-flex items-center justify-center gap-1.5">
                      <X size={15} /> Rad
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
