/**
 * Balance top-up, entirely inside the Mini App.
 *
 * The user used to be sent to the bot to send a photo. Here they pick a
 * card, choose an image from the gallery or camera, see it, replace it if
 * it came out wrong, and submit — without leaving the app.
 *
 * `capture` is deliberately absent from the file input: adding it forces
 * the camera on mobile and hides the gallery, and most people photograph
 * a receipt once and then pick that photo.
 */
import { AlertTriangle, Image as ImageIcon, RefreshCw, Trash2, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { useT } from "../lib/i18n";
import type { PaymentCard } from "../types/movie";

interface Props {
  onClose: () => void;
  onSubmitted: (message: string) => void;
  /** Pre-fills the amount when topping up to reach a specific plan. */
  suggestedAmount?: number;
}

export function TopUpSheet({ onClose, onSubmitted, suggestedAmount }: Props) {
  const t = useT();
  const [cards, setCards] = useState<PaymentCard[]>([]);
  const [cardId, setCardId] = useState<number | null>(null);
  const [amount, setAmount] = useState(suggestedAmount ? String(Math.ceil(suggestedAmount)) : "");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [cardsLoading, setCardsLoading] = useState(true);
  const fileInput = useRef<HTMLInputElement | null>(null);
  /**
   * Synchronous double-submit latch.
   *
   * `disabled={busy}` alone is not a guard: React applies state after a
   * re-render, so two taps inside one frame both observe `busy === false`
   * and both fire — two receipts for one real payment, which an admin can
   * approve twice. A ref is written immediately. The server refuses a
   * duplicate pending top-up as well; this only stops the request being
   * made twice in the first place.
   */
  const submitting = useRef(false);

  useEffect(() => {
    api
      .paymentCards()
      .then((result) => {
        setCards(result);
        setCardId(result[0]?.id ?? null);
      })
      .catch(() => setCards([]))
      .finally(() => setCardsLoading(false));
  }, []);

  // Object URLs are leaked memory until revoked, and replacing the image
  // creates a new one every time.
  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const submit = async () => {
    if (!cardId || !file || !Number(amount)) return;
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    try {
      const response = await api.submitTopup(Number(amount), cardId, file);
      onSubmitted(response.message);
      onClose();
    } catch (err) {
      setError(
        err instanceof ApiError && err.isRateLimited
          ? t("app.rate_limited")
          : err instanceof ApiError
            ? err.message
            : t("app.generic_error"),
      );
    } finally {
      submitting.current = false;
      setBusy(false);
      setConfirming(false);
    }
  };

  // Everything the user must have read before money leaves their account.
  // A blocking step rather than fine print: the payment cannot be reversed,
  // and the most common support case is a declared amount that does not
  // match the receipt.
  const confirmation = (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6">
      <div className="w-full max-w-xs space-y-3 rounded-2xl bg-surface p-4">
        <h3 className="font-display text-base font-semibold text-ink">
          {t("payment.warning_title")}
        </h3>
        <ul className="space-y-2">
          {[
            "payment.warning_nonrefundable",
            "payment.warning_exact",
            "payment.warning_receipt",
            "payment.warning_review",
          ].map((key) => (
            <li key={key} className="flex gap-2 font-body text-xs leading-relaxed text-ink-dim">
              <AlertTriangle size={13} className="mt-0.5 shrink-0 text-marquee" />
              <span>{t(key)}</span>
            </li>
          ))}
        </ul>
        <p className="rounded-lg bg-surface-hi px-3 py-2 text-center font-mono text-sm text-ink">
          {Number(amount).toLocaleString()}
        </p>
        <button
          onClick={() => void submit()}
          disabled={busy}
          className="w-full rounded-full bg-marquee py-3 font-semibold text-on-marquee disabled:opacity-50"
        >
          {t("payment.confirm_understand")}
        </button>
        <button
          onClick={() => setConfirming(false)}
          disabled={busy}
          className="w-full rounded-full border border-surface-hi py-2.5 font-body text-sm text-ink-dim"
        >
          {t("app.cancel")}
        </button>
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-40 flex items-end bg-black/60" onClick={onClose}>
      <div
        onClick={(event) => event.stopPropagation()}
        className="max-h-[90vh] w-full space-y-4 overflow-y-auto rounded-t-2xl bg-surface p-4 pb-[calc(5rem_+_env(safe-area-inset-bottom))] shadow-2xl"
      >
        <div className="flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold text-ink">{t("app.topup")}</h2>
          <button onClick={onClose} aria-label={t("app.close")} className="text-ink-dim">
            <X size={20} />
          </button>
        </div>

        {error && (
          <p className="rounded-lg bg-red-500/10 px-3 py-2 font-body text-sm text-red-400">
            {error}
          </p>
        )}

        <div>
          <label className="mb-1 block font-body text-xs text-ink-dim">{t("app.amount")}</label>
          <input
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ""))}
            inputMode="numeric"
            className="w-full rounded-xl border border-surface-hi bg-bg px-3 py-2.5 font-mono text-sm text-ink focus:border-marquee focus:outline-none"
          />
        </div>

        <div>
          <p className="mb-1.5 font-body text-xs leading-relaxed text-ink-dim">
            {t("payment.instructions")}
          </p>
          <p className="mb-1.5 font-body text-xs text-ink-dim">{t("app.select_card")}</p>
          {cardsLoading && (
            <p className="font-body text-xs text-ink-dim">{t("app.cards_loading")}</p>
          )}
          {!cardsLoading && cards.length === 0 && (
            <p className="rounded-lg border border-surface-hi bg-bg px-3 py-2 font-body text-xs text-ink-dim">
              {t("app.cards_empty")}
            </p>
          )}
          <div className="space-y-1.5">
            {cards.map((card) => (
              <button
                key={card.id}
                onClick={() => setCardId(card.id)}
                className={`flex w-full items-center justify-between rounded-xl border px-3 py-2.5 text-left transition-colors ${
                  cardId === card.id
                    ? "border-marquee bg-surface-hi"
                    : "border-surface-hi bg-bg"
                }`}
              >
                <span className="min-w-0">
                  <span className="block truncate font-body text-sm text-ink">
                    {card.bank_name ?? card.holder_name}
                  </span>
                  <span className="block font-mono text-[11px] text-ink-dim">
                    {card.card_number}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <div>
          <input
            ref={fileInput}
            type="file"
            // Broad on purpose, exactly as the admin poster picker is: a
            // Telegram WebView gallery filtered to three MIME types hides
            // HEIC photos and the many entries reported as
            // application/octet-stream, so the receipt could not be picked
            // at all. The backend decodes and re-encodes the bytes, which is
            // the real check — and the payment flow behind it is untouched.
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />

          {previewUrl ? (
            <div className="space-y-2">
              {/* Preview before submitting — a blurred receipt is the most
                  common reason a top-up is rejected. */}
              <img
                src={previewUrl}
                alt=""
                className="max-h-64 w-full rounded-xl border border-surface-hi object-contain"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => fileInput.current?.click()}
                  className="flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-surface-hi py-2.5 font-body text-sm text-ink"
                >
                  <RefreshCw size={14} /> {t("app.replace_image")}
                </button>
                {/* Remove, not just replace: a user who picked the wrong
                    photo should be able to back out without submitting it. */}
                <button
                  onClick={() => {
                    setFile(null);
                    if (fileInput.current) fileInput.current.value = "";
                  }}
                  aria-label={t("app.remove_image")}
                  className="rounded-xl border border-surface-hi px-3 text-ink-dim"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => fileInput.current?.click()}
              className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-surface-hi py-6 font-body text-sm text-ink-dim"
            >
              <ImageIcon size={16} /> {t("app.choose_image")}
            </button>
          )}
        </div>

        <button
          onClick={() => setConfirming(true)}
          disabled={busy || !file || !cardId || !Number(amount)}
          className="flex w-full items-center justify-center gap-1.5 rounded-full bg-marquee py-3 font-semibold text-on-marquee shadow-marquee transition-transform active:scale-95 disabled:opacity-50"
        >
          <Upload size={16} /> {t("app.submit")}
        </button>
      </div>
      {confirming && confirmation}
    </div>
  );
}
