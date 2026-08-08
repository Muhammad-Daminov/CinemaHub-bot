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
    </div>
  );
}
