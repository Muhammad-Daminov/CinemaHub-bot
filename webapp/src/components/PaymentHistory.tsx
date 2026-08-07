/**
 * The user's own payment history.
 *
 * Combines settled ledger entries with receipts still awaiting review.
 * Pending receipts have not moved the balance yet, so a user who has just
 * submitted one would otherwise see nothing here and submit it again.
 *
 * History is permanent — the receipt *image* is purged after 30 days, the
 * record of the payment is not. That asymmetry is why this list shows
 * amounts and statuses rather than thumbnails.
 */
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useT } from "../lib/i18n";
import type { PaymentHistoryEntry } from "../types/movie";

export function PaymentHistory() {
  const t = useT();
  const [entries, setEntries] = useState<PaymentHistoryEntry[] | null>(null);

  useEffect(() => {
    api
      .paymentHistory()
      .then(setEntries)
      .catch(() => setEntries([]));
  }, []);

  if (entries === null) {
    return <p className="p-4 text-center font-body text-sm text-ink-dim">{t("app.loading")}</p>;
  }

  return (
    <section>
      <h2 className="mb-1 font-display text-sm font-medium tracking-wide text-ink-dim">
        {t("app.history_title")}
      </h2>

      {entries.length === 0 ? (
        <p className="rounded-xl border border-surface-hi bg-surface p-3 font-body text-sm text-ink-dim">
          {t("app.history_empty")}
        </p>
      ) : (
        <ul className="rounded-xl border border-surface-hi bg-surface px-3">
          {entries.map((entry) => {
            const pending = entry.kind === "pending_receipt";
            return (
              <li
                key={`${entry.kind}-${entry.id}`}
                className="flex items-center justify-between gap-3 border-b border-surface-hi py-2.5 last:border-b-0"
              >
                <span className="min-w-0">
                  <span className="block truncate font-body text-sm text-ink">
                    {pending ? t("app.pending_review") : entry.description ?? entry.kind}
                  </span>
                  <span className="block font-mono text-[11px] text-ink-dim">
                    {new Date(entry.created_at).toLocaleDateString()}
                  </span>
                </span>
                {/* Signed: a purchase is negative in the ledger, and showing
                    it unsigned would read as money received. */}
                <span
                  className={`shrink-0 font-mono text-sm ${
                    pending ? "text-ink-dim" : entry.amount < 0 ? "text-ink" : "text-marquee"
                  }`}
                >
                  {entry.amount > 0 ? "+" : ""}
                  {entry.amount.toFixed(2)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
