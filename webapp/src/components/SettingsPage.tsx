/**
 * Account overview + the language switch.
 *
 * The language control writes to the same chp_users.language column the
 * bot reads, so changing it here also changes the bot's replies. That is
 * the feature, not a side effect — one setting, two surfaces.
 */
import { Check, Copy, Plus, Star } from "lucide-react";
import { useState } from "react";
import { LANGUAGES, useT, type Language } from "../lib/i18n";
import type { UserProfile } from "../types/movie";
import { PaymentHistory } from "./PaymentHistory";

interface Props {
  profile: UserProfile | null;
  onChangeLanguage: (language: Language) => Promise<void>;
  onOpenPlans: () => void;
  /**
   * Opens the canonical top-up sheet directly.
   *
   * Separate from `onOpenPlans` on purpose: adding funds and buying a
   * subscription are two different intentions, and collapsing them meant
   * the only route to paying was to attempt a purchase you could not
   * afford and read the error.
   */
  onOpenTopUp: () => void;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-surface-hi py-2.5 last:border-b-0">
      <span className="font-body text-sm text-ink-dim">{label}</span>
      <span className="truncate font-body text-sm text-ink">{value}</span>
    </div>
  );
}

export function SettingsPage({ profile, onChangeLanguage, onOpenPlans, onOpenTopUp }: Props) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);

  if (!profile) {
    return <p className="p-4 text-center font-body text-sm text-ink-dim">{t("app.loading")}</p>;
  }

  const copyReferral = async () => {
    try {
      await navigator.clipboard.writeText(profile.referral_code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard is permission-gated in some webviews; the code is
      // selectable on screen either way, so a failure needs no alarm.
    }
  };

  const choose = async (language: Language) => {
    if (language === profile.language) return;
    setSaving(true);
    try {
      await onChangeLanguage(language);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5 p-4">
      <h1 className="font-display text-xl font-semibold text-ink">{t("app.settings_title")}</h1>

      <section>
        <h2 className="mb-1 font-display text-sm font-medium tracking-wide text-ink-dim">
          {t("app.settings_account")}
        </h2>
        <div className="rounded-xl border border-surface-hi bg-surface px-3">
          <Row label={t("app.settings_name")} value={profile.full_name ?? "—"} />
          <Row label={t("app.settings_telegram_id")} value={String(profile.telegram_id)} />
          <Row
            label={t("app.settings_premium")}
            value={t(profile.is_premium ? "app.settings_premium_yes" : "app.settings_premium_no")}
          />
        </div>
      </section>

      {/*
        Balance and the two things you can do with it, named.

        This used to be a single row whose only affordance was a chevron
        beside a number: tapping it opened the plan catalogue, and the
        only way to reach top-up at all was to attempt a purchase you
        could not afford. Money should not be a puzzle — the balance is
        stated, and both actions say what they do.
      */}
      <section>
        <h2 className="mb-1 font-display text-sm font-medium tracking-wide text-ink-dim">
          {t("app.balance_title")}
        </h2>
        <div className="space-y-2 rounded-xl border border-surface-hi bg-surface p-3">
          <p className="font-display text-2xl font-semibold text-marquee">
            {profile.balance.toFixed(2)}
          </p>
          <p className="font-body text-[11px] text-ink-dim">{t("app.balance_hint")}</p>

          <button
            onClick={onOpenTopUp}
            className="flex w-full items-center justify-center gap-1.5 rounded-full bg-marquee py-2.5 font-body text-sm font-semibold text-on-marquee transition-transform active:scale-95"
          >
            <Plus size={16} /> {t("app.balance_topup_action")}
          </button>
          <button
            onClick={onOpenPlans}
            className="flex w-full items-center justify-center gap-1.5 rounded-full border border-surface-hi py-2.5 font-body text-sm text-ink transition-transform active:scale-95"
          >
            <Star size={15} /> {t("app.balance_buy_plan")}
          </button>
        </div>
      </section>

      <section>
        <h2 className="mb-1 font-display text-sm font-medium tracking-wide text-ink-dim">
          {t("app.settings_referral")}
        </h2>
        <div className="flex items-center gap-2 rounded-xl border border-surface-hi bg-surface p-3">
          <code className="flex-1 truncate font-mono text-sm text-marquee">
            {profile.referral_code}
          </code>
          <button
            onClick={copyReferral}
            className="flex shrink-0 items-center gap-1.5 rounded-full bg-surface-hi px-3 py-1.5 font-body text-xs text-ink transition-transform active:scale-95"
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {t(copied ? "app.settings_copied" : "app.settings_copy")}
          </button>
        </div>
      </section>

      <PaymentHistory />

      <section>
        <h2 className="mb-1 font-display text-sm font-medium tracking-wide text-ink-dim">
          {t("app.settings_language")}
        </h2>
        <div className="flex flex-col gap-2">
          {LANGUAGES.map((option) => {
            const active = option.value === profile.language;
            return (
              <button
                key={option.value}
                onClick={() => choose(option.value)}
                disabled={saving}
                className={`flex items-center justify-between rounded-xl border px-4 py-2.5 font-body text-sm transition-colors disabled:opacity-50 ${
                  active
                    ? "border-marquee bg-surface text-marquee"
                    : "border-surface-hi bg-surface text-ink"
                }`}
              >
                {t(option.labelKey)}
                {active && <Check size={15} />}
              </button>
            );
          })}
        </div>
      </section>

      {/* Last, and deliberately plain. It exists so a deploy can be
          confirmed from the phone that is actually running the app —
          `__APP_VERSION__` is compiled into this bundle, so what it shows
          is the build in front of you rather than what the server says it
          serves. `/health` reports the same number as `app_version`. */}
      <section>
        <h2 className="mb-1 font-display text-sm font-medium tracking-wide text-ink-dim">
          {t("app.settings_about")}
        </h2>
        <div className="rounded-xl border border-surface-hi bg-surface px-3">
          <Row label={t("app.settings_version")} value={__APP_VERSION__} />
        </div>
      </section>
    </div>
  );
}
