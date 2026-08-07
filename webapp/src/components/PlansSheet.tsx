/**
 * Subscription plans: compare, then buy — without leaving the Mini App.
 *
 * What each plan *would do* comes from the backend's preview, not from a
 * rule reimplemented here. Extend / upgrade / queue depends on relative
 * priority and on what the user already holds, and a second copy of that
 * logic in the client would eventually disagree with the one that
 * actually charges people.
 *
 * The insufficient-balance dialog shows current, required and missing,
 * with Top Up wired straight to the top-up sheet — the user is never
 * dropped back at the balance screen to work it out themselves.
 */
import { Check, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { useT } from "../lib/i18n";
import type { BillingOverview, BillingPlan, PurchasePreview } from "../types/movie";
import { TopUpSheet } from "./TopUpSheet";

interface Props {
  onClose: () => void;
  onToast: (message: string, tone: "success" | "error") => void;
}

const OUTCOME_KEY: Record<string, string> = {
  extend: "app.plan_extends",
  upgrade: "app.plan_upgrades",
  queued: "app.plan_queues",
};

export function PlansSheet({ onClose, onToast }: Props) {
  const t = useT();
  const [overview, setOverview] = useState<BillingOverview | null>(null);
  const [previews, setPreviews] = useState<Record<number, PurchasePreview>>({});
  const [shortfall, setShortfall] = useState<{ balance: number; required: number; missing: number } | null>(null);
  const [topUpFor, setTopUpFor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const data = await api.billingOverview();
      setOverview(data);
      // One preview per sellable plan, so each card can say what buying it
      // would actually do before the user commits.
      const entries = await Promise.all(
        data.plans
          .filter((p) => !p.is_free)
          .map(async (p) => [p.id, await api.previewPurchase(p.id)] as const),
      );
      setPreviews(Object.fromEntries(entries));
    } catch {
      setOverview(null);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const buy = async (plan: BillingPlan) => {
    setBusy(true);
    try {
      await api.purchasePlan(plan.id);
      onToast(t("app.plan_current") + ": " + plan.name, "success");
      await reload();
    } catch (err) {
      // 402 carries the three numbers the dialog is required to show, so
      // they are rendered rather than recomputed.
      if (err instanceof ApiError && err.status === 402) {
        try {
          const detail = JSON.parse(err.message) as never;
          setShortfall(detail);
        } catch {
          const raw = err as unknown as { detail?: { balance: number; required: number; missing: number } };
          if (raw.detail) setShortfall(raw.detail);
          else onToast(t("app.balance_insufficient"), "error");
        }
      } else {
        onToast(err instanceof ApiError ? err.message : t("app.generic_error"), "error");
      }
    } finally {
      setBusy(false);
    }
  };

  const allFeatures = Array.from(
    new Map(
      (overview?.plans ?? []).flatMap((p) => p.features.map((f) => [f.code, f] as const)),
    ).values(),
  );

  return (
    <div className="fixed inset-0 z-30 flex items-end bg-black/60" onClick={onClose}>
      <div
        onClick={(event) => event.stopPropagation()}
        className="max-h-[90vh] w-full space-y-4 overflow-y-auto rounded-t-2xl bg-surface p-4 pb-[calc(5rem_+_env(safe-area-inset-bottom))] shadow-2xl"
      >
        <div className="flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold text-ink">{t("app.plans_title")}</h2>
          <button onClick={onClose} aria-label={t("app.close")} className="text-ink-dim">
            <X size={20} />
          </button>
        </div>

        {overview && (
          <div className="rounded-xl border border-surface-hi bg-bg p-3">
            <p className="font-mono text-[11px] uppercase tracking-wider text-ink-dim">
              {t("app.balance_current")}
            </p>
            <p className="font-display text-xl text-ink">{overview.balance.toFixed(2)}</p>
            {overview.current && (
              <p className="mt-1 font-body text-xs text-ink-dim">
                {t("app.plan_current")}: {overview.current.plan_name} ·{" "}
                {new Date(overview.current.expires_at).toLocaleDateString()}
              </p>
            )}
            {overview.queued.map((q) => (
              <p key={q.started_at} className="font-body text-xs text-ink-dim">
                {t("app.plan_queued")}: {q.plan_name} ·{" "}
                {new Date(q.started_at).toLocaleDateString()}
              </p>
            ))}
          </div>
        )}

        {(overview?.plans ?? [])
          .filter((plan) => !plan.is_free)
          .map((plan) => {
            const preview = previews[plan.id];
            return (
              <div key={plan.id} className="rounded-xl border border-surface-hi bg-bg p-3">
                <div className="mb-1 flex items-baseline justify-between gap-2">
                  <h3 className="font-display text-base text-ink">{plan.name}</h3>
                  <span className="font-mono text-sm text-marquee">
                    {plan.price.toFixed(0)}
                  </span>
                </div>
                <p className="font-mono text-[11px] text-ink-dim">{plan.duration_days} days</p>

                {plan.benefits.length > 0 && (
                  <ul className="mt-2 space-y-0.5">
                    {plan.benefits.map((benefit) => (
                      <li key={benefit} className="flex gap-1.5 font-body text-xs text-ink-dim">
                        <Check size={12} className="mt-0.5 shrink-0 text-marquee" />
                        {benefit}
                      </li>
                    ))}
                  </ul>
                )}

                {/* Comparison matrix: every feature any plan offers, with
                    this plan's answer — so a gap is visible, not implied. */}
                {allFeatures.length > 0 && (
                  <div className="mt-2 space-y-0.5 border-t border-surface-hi pt-2">
                    {allFeatures.map((feature) => {
                      const granted = plan.features.find((f) => f.code === feature.code);
                      return (
                        <div
                          key={feature.code}
                          className="flex items-center justify-between font-body text-[11px]"
                        >
                          <span className={granted ? "text-ink" : "text-ink-dim line-through"}>
                            {feature.name}
                          </span>
                          <span className="font-mono text-ink-dim">
                            {granted ? granted.value ?? "✓" : "—"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}

                {preview && OUTCOME_KEY[preview.outcome] && (
                  <p className="mt-2 font-body text-[11px] text-marquee">
                    {t(OUTCOME_KEY[preview.outcome])}
                  </p>
                )}

                <button
                  onClick={() => void buy(plan)}
                  disabled={busy}
                  className="mt-2 w-full rounded-full bg-marquee py-2.5 font-semibold text-on-marquee transition-transform active:scale-95 disabled:opacity-50"
                >
                  {t("app.buy")}
                </button>
              </div>
            );
          })}
      </div>

      {shortfall && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="w-full max-w-xs space-y-3 rounded-2xl bg-surface p-4">
            <p className="font-body text-sm text-ink">{t("app.balance_insufficient")}</p>
            <dl className="space-y-1 font-mono text-xs">
              {[
                [t("app.balance_current"), shortfall.balance],
                [t("app.balance_required"), shortfall.required],
                [t("app.balance_missing"), shortfall.missing],
              ].map(([label, value]) => (
                <div key={String(label)} className="flex justify-between">
                  <dt className="text-ink-dim">{label}</dt>
                  <dd className="text-ink">{Number(value).toFixed(2)}</dd>
                </div>
              ))}
            </dl>
            <button
              onClick={() => {
                setTopUpFor(shortfall.missing);
                setShortfall(null);
              }}
              className="w-full rounded-full bg-marquee py-2.5 font-semibold text-on-marquee"
            >
              {t("app.topup")}
            </button>
            <button
              onClick={() => setShortfall(null)}
              className="w-full rounded-full border border-surface-hi py-2.5 font-body text-sm text-ink-dim"
            >
              {t("app.cancel")}
            </button>
          </div>
        </div>
      )}

      {topUpFor !== null && (
        <TopUpSheet
          suggestedAmount={topUpFor}
          onClose={() => setTopUpFor(null)}
          onSubmitted={(message) => {
            onToast(message, "success");
            void reload();
          }}
        />
      )}
    </div>
  );
}
