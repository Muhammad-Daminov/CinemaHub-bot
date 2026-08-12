/**
 * The join-the-channel gate.
 *
 * Shown when a watch is refused because the viewer has not joined the
 * required channel. Previously that refusal was a toast: it told the user
 * what was wrong and gave them nowhere to go, so the only way forward was
 * to leave the app and find the channel themselves.
 *
 * Two actions, in the order they have to happen: open the channel, then
 * come back and re-check. The check is a *server* call — the button does
 * not mark the user as joined, it asks Telegram again.
 *
 * The channel and its link come from the backend, which reads the one
 * existing configuration in `chp_system_settings`. Nothing here is
 * hardcoded, and there is no second place a channel address lives.
 */
import { X } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { useT } from "../lib/i18n";
import { openLink } from "../lib/telegram";
import type { MembershipStatus } from "../types/movie";

interface Props {
  status: MembershipStatus;
  /** Called once the server confirms the join, so the caller can resume. */
  onJoined: () => void;
  onClose: () => void;
}

export function MembershipGate({ status, onJoined, onClose }: Props) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const check = async () => {
    setBusy(true);
    setFailed(false);
    try {
      const fresh = await api.recheckMembership();
      if (fresh.is_member) {
        onJoined();
        return;
      }
      // Still outside. The gate stays up — closing it here would look like
      // success and drop the action the viewer was trying to take.
      setFailed(true);
    } catch (error) {
      setFailed(!(error instanceof ApiError) || !error.isRateLimited);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 sm:items-center">
      <div className="w-full max-w-md rounded-t-2xl bg-surface p-5 pb-8 sm:rounded-2xl sm:pb-5">
        <div className="mb-3 flex items-start justify-between gap-3">
          <h2 className="font-display text-base font-semibold text-ink">
            {t("app.membership_title")}
          </h2>
          <button onClick={onClose} aria-label={t("app.close")} className="text-ink-dim hover:text-ink">
            <X size={20} />
          </button>
        </div>

        <p className="mb-3 font-body text-sm leading-relaxed text-ink-dim">
          {t("app.membership_text")}
        </p>

        {status.channel && (
          <p className="mb-4 rounded-xl bg-surface-hi px-3 py-2 text-center font-mono text-sm text-ink">
            {status.channel}
          </p>
        )}

        {failed && (
          <p className="mb-3 rounded-xl bg-surface-hi px-3 py-2 font-body text-xs text-premiere">
            {t("app.membership_still_not")}
          </p>
        )}

        {/* Only offered when the configuration can actually produce a link.
            A numeric chat id has no public URL, so the channel is named
            above and the button is omitted rather than pointing nowhere. */}
        {status.invite_url && (
          <button
            onClick={() => openLink(status.invite_url as string)}
            className="mb-2 w-full rounded-full bg-marquee py-3 font-semibold text-on-marquee shadow-marquee transition-transform active:scale-95"
          >
            {t("app.membership_join")}
          </button>
        )}

        <button
          onClick={check}
          disabled={busy}
          className="w-full rounded-full border border-surface-hi bg-surface py-3 font-semibold text-ink transition-transform active:scale-95 disabled:opacity-60"
        >
          {t("app.membership_check")}
        </button>
      </div>
    </div>
  );
}
