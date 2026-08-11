/**
 * Platform settings. Currently one: the channel a user must join before
 * a video will be delivered to them.
 *
 * It lives here rather than in an environment variable because turning
 * the requirement on, off, or onto a different channel is an operational
 * decision — and configuration that needs a redeploy is configuration
 * that waits for whoever has deploy access.
 *
 * The panel says plainly what enabling it does and what happens when the
 * bot cannot see the channel, because both are invisible from the admin's
 * own account: administrators are exempt from the check, so an operator
 * testing it on themselves would conclude nothing is happening.
 */
import { useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import { Button, Field, Notice, SectionTitle, TextInput } from "./ui";

export function SettingsPanel() {
  const [enabled, setEnabled] = useState(false);
  const [channel, setChannel] = useState("");
  const [hasInviteUrl, setHasInviteUrl] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    adminApi
      .membershipSettings()
      .then((settings) => {
        setEnabled(settings.require_membership);
        setChannel(settings.required_channel ?? "");
        setHasInviteUrl(settings.has_invite_url);
      })
      .catch(() => undefined);
  }, []);

  const save = async () => {
    setBusy(true);
    try {
      const saved = await adminApi.saveMembershipSettings(enabled, channel.trim() || null);
      setHasInviteUrl(saved.has_invite_url);
      setError(null);
      setMessage("Saqlandi.");
    } catch (err) {
      setMessage(null);
      setError(err instanceof ApiError ? err.message : "Saqlashda xatolik.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <SectionTitle>Majburiy obuna</SectionTitle>
      {error && <Notice message={error} tone="error" />}
      {message && <Notice message={message} />}

      <div className="rounded-xl border border-surface-hi bg-surface p-3">
        <label className="flex items-center justify-between gap-3">
          <span className="font-body text-sm text-ink">Kanalga obuna talab qilinsin</span>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
            className="h-5 w-5 accent-marquee"
          />
        </label>
        <p className="mt-2 font-body text-xs text-ink-dim">
          Yoqilganda foydalanuvchi kanalga qo'shilmaguncha video yubormaydi. Katalogni ko'rish
          ochiq qoladi. Adminlarga taalluqli emas.
        </p>
      </div>

      <Field label="Kanal (@nomi yoki havola)">
        <TextInput value={channel} onChange={setChannel} placeholder="@cinemahub" mono />
      </Field>

      {channel.trim() && !hasInviteUrl && (
        <Notice
          message="Bu kanal uchun havola yasab bo'lmaydi — @nom ishlating, aks holda foydalanuvchi qo'shilish tugmasini ko'rmaydi."
          tone="error"
        />
      )}

      <Notice message="Bot kanalda administrator bo'lishi shart. Aks holda tekshiruv o'tkazib yuboriladi va hamma kirita oladi." />

      <Button full disabled={busy} onClick={save}>
        Saqlash
      </Button>

      <TrialSettingsSection />
    </div>
  );
}

/**
 * The new-user trial: whether a person who has just started the bot is
 * given a subscription, and for how long.
 *
 * Its own component with its own request state rather than folded into
 * the panel above, so a failure saving the channel cannot look like a
 * failure saving the trial. The two settings share a screen, nothing else.
 *
 * Kept off by default on the server. This UI states plainly that the
 * setting applies to *future* signups only — an operator raising the
 * duration from 3 to 7 days would otherwise reasonably expect existing
 * trials to lengthen, and they do not.
 */
function TrialSettingsSection() {
  const [enabled, setEnabled] = useState(false);
  const [days, setDays] = useState("3");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi
      .trialSettings()
      .then((settings) => {
        setEnabled(settings.enabled);
        setDays(String(settings.days));
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Sozlamani o'qib bo'lmadi."),
      )
      .finally(() => setLoading(false));
  }, []);

  // Validated here as well as on the server, which bounds it to 1–365.
  // The client check exists to explain the rule before a round trip, not
  // to enforce it — the server refuses a bad value either way.
  const parsedDays = Number(days);
  const daysValid = Number.isInteger(parsedDays) && parsedDays >= 1 && parsedDays <= 365;

  const save = async () => {
    if (!daysValid) return;
    setBusy(true);
    try {
      const saved = await adminApi.saveTrialSettings(enabled, parsedDays);
      setEnabled(saved.enabled);
      setDays(String(saved.days));
      setError(null);
      setMessage("Saqlandi.");
    } catch (err) {
      setMessage(null);
      setError(err instanceof ApiError ? err.message : "Saqlashda xatolik.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-6 space-y-3 border-t border-surface-hi pt-6">
      <SectionTitle>Sinov obunasi</SectionTitle>
      {error && <Notice message={error} tone="error" />}
      {message && <Notice message={message} />}

      {loading ? (
        <p className="font-body text-sm text-ink-dim">Yuklanmoqda…</p>
      ) : (
        <>
          <div className="rounded-xl border border-surface-hi bg-surface p-3">
            <label className="flex items-center justify-between gap-3">
              <span className="font-body text-sm text-ink">Yangi foydalanuvchiga sinov berilsin</span>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
                className="h-5 w-5 accent-marquee"
              />
            </label>
            <p className="mt-2 font-body text-xs text-ink-dim">
              Yoqilganda botni birinchi marta ishga tushirgan foydalanuvchi shuncha kunlik premium
              obuna oladi. Sinov obunasi premium kinolarni ham ochadi.
            </p>
          </div>

          <Field label="Sinov muddati (kun)">
            <TextInput value={days} onChange={setDays} placeholder="3" mono />
          </Field>

          {!daysValid && (
            <Notice message="Muddat 1 dan 365 gacha butun son bo'lishi kerak." tone="error" />
          )}

          <Notice message="O'zgarish faqat yangi foydalanuvchilarga taalluqli. Mavjud obunalar o'zgarmaydi. Har bir foydalanuvchi sinovni faqat bir marta oladi." />

          <Button full disabled={busy || !daysValid} onClick={save}>
            {busy ? "Saqlanmoqda…" : "Saqlash"}
          </Button>
        </>
      )}
    </div>
  );
}
