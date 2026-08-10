/**
 * Composing a broadcast: audience, targeting, three languages, media,
 * preview, and a confirmation the operator has to mean.
 *
 * The governing idea is that **this form is not authoritative about
 * anything**. It validates for the operator's benefit — a disabled button
 * beats a 422 — but every rule it applies is applied again server-side,
 * and the numbers it shows all come from the backend. Specifically:
 *
 *  - The audience vocabulary and the recipient estimate are fetched, never
 *    computed here. There is no client-side counting of users, and no way
 *    to name one: the payload has fields for a segment and a target, and
 *    nothing that could carry a user id.
 *  - The target lists come from `GET /admin/broadcasts/targets`, which is
 *    served from the same allowlists that validate a create. The panel
 *    therefore cannot offer a choice the API would refuse.
 *  - Media is a Telegram `file_id` the admin captured by forwarding the
 *    file to the bot. Nothing is uploaded, downloaded or proxied here.
 *
 * State is local to this component. No global draft, no store, no
 * `localStorage` — a half-written broadcast is not something to persist to
 * disk on a shared device.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { adminApi, ApiError } from "../lib/api";
import { useT } from "../lib/i18n";
import type {
  AdminBroadcast,
  BroadcastAudience,
  BroadcastInput,
  BroadcastLanguage,
  BroadcastMedia,
  BroadcastTargets,
} from "../types/admin";
import { BroadcastPreview } from "./BroadcastPreview";
import { Button, Field, Notice, SectionTitle, TextArea, TextInput } from "./ui";

/**
 * Mirrors the backend's audience enum. Labels are the panel's own Uzbek.
 *
 * Exported because the history and the progress screen must name an
 * audience the same way the composer does — one map, so a segment cannot
 * be called two different things on two screens.
 */
export const AUDIENCES: { value: BroadcastAudience; label: string; hint: string }[] = [
  { value: "all", label: "Hammaga", hint: "Barcha foydalanuvchilar" },
  { value: "premium", label: "Obunachilarga", hint: "Faol obunasi borlar" },
  { value: "free", label: "Obunasizlarga", hint: "Obunasi yo'qlar" },
  { value: "interest", label: "Qiziqish bo'yicha", hint: "Ko'p ko'radigan turi bo'yicha" },
  { value: "badge", label: "Nishon bo'yicha", hint: "Erishilgan nishon bo'yicha" },
];

const LANGUAGES: { value: BroadcastLanguage; label: string }[] = [
  { value: "uz", label: "O'zbek" },
  { value: "ru", label: "Русский" },
  { value: "en", label: "English" },
];

export const MEDIA_TYPES: { value: BroadcastMedia; label: string }[] = [
  { value: "none", label: "Matn" },
  { value: "photo", label: "Rasm" },
  { value: "video", label: "Video" },
];

/**
 * Telegram's limits, mirrored for the character counter only.
 *
 * The backend enforces these and rejects anything past them; these copies
 * exist so the operator sees the wall before they hit it, never as the
 * decision. If the two ever disagree the server wins, which is why the
 * counter warns rather than blocks.
 */
const MAX_MESSAGE = 4096;
const MAX_CAPTION = 1024;

/** Long enough that typing a target does not fire a request per keystroke. */
const ESTIMATE_DEBOUNCE_MS = 400;

/** Content types have Uzbek labels already; badges live in the locale catalog. */
const INTEREST_LABELS: Record<string, string> = {
  film: "Kino",
  serial: "Serial",
  multfilm: "Multfilm",
  anime: "Anime",
  drama: "Drama",
};

type Draft = {
  audience: BroadcastAudience;
  targetValue: string;
  mediaType: BroadcastMedia;
  mediaFileId: string;
  bodies: Record<BroadcastLanguage, string>;
};

const EMPTY_DRAFT: Draft = {
  audience: "all",
  targetValue: "",
  mediaType: "none",
  mediaFileId: "",
  bodies: { uz: "", ru: "", en: "" },
};

function needsTarget(audience: BroadcastAudience): boolean {
  return audience === "interest" || audience === "badge";
}

/**
 * Names a target the way an operator would say it: "Anime", not "anime";
 * "Anime kashfiyotchi va yuqori", not "badge.anime.".
 *
 * Badge names come from the locale catalog rather than a second hardcoded
 * table — the badge vocabulary already exists there, and duplicating it
 * here is exactly the drift this phase was built to avoid. An unknown key
 * degrades to itself rather than to blank, so a target added server-side
 * shows up as its raw value instead of disappearing.
 *
 * Shared by the composer, the confirmation and the history, so a target
 * cannot be described one way while being chosen and another afterwards.
 */
export function useTargetLabel(): (audience: BroadcastAudience, value: string | null) => string {
  const t = useT();
  return useCallback(
    (audience, value) => {
      if (!value) return "";
      if (audience === "interest") return INTEREST_LABELS[value] ?? value;
      if (audience !== "badge") return value;

      // A family prefix is not itself a catalog key, so it is described by
      // its lowest tier plus a marker.
      if (value.endsWith(".")) {
        const family = t(`${value}1`);
        return family === `${value}1` ? value : `${family} va yuqori`;
      }
      const label = t(value);
      return label === value ? value : label;
    },
    [t],
  );
}

export function BroadcastComposer({ onSent }: { onSent: (created: AdminBroadcast) => void }) {
  const targetLabel = useTargetLabel();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [language, setLanguage] = useState<BroadcastLanguage>("uz");
  const [targets, setTargets] = useState<BroadcastTargets | null>(null);
  const [estimate, setEstimate] = useState<number | null>(null);
  const [estimateState, setEstimateState] = useState<"idle" | "loading" | "error">("idle");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * Guards the send against a double tap.
   *
   * A ref rather than the `busy` state: state updates are batched, so two
   * taps inside one render pass would both observe `busy === false` and
   * both fire. The ref is written synchronously. The backend refuses an
   * identical queued broadcast as well — this is the fast path, not the
   * guarantee.
   */
  const sending = useRef(false);

  useEffect(() => {
    let cancelled = false;
    adminApi
      .broadcastTargets()
      .then((value) => {
        if (!cancelled) setTargets(value);
      })
      .catch(() => {
        /* the audience selector still works; targeted options stay empty */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const targetOptions = useMemo(() => {
    if (!targets) return [];
    if (draft.audience === "interest") return targets.interests;
    if (draft.audience === "badge") return [...targets.badge_families, ...targets.badges];
    return [];
  }, [targets, draft.audience]);

  const targetReady = !needsTarget(draft.audience) || draft.targetValue !== "";

  /**
   * Re-quotes the audience whenever anything eligibility-affecting changes.
   *
   * Debounced and cancellation-guarded: a fast switch between audiences
   * fires several requests, and without the flag a slow earlier one could
   * land after a fast later one and display the wrong number.
   */
  useEffect(() => {
    if (!targetReady) {
      setEstimate(null);
      setEstimateState("idle");
      return;
    }
    let cancelled = false;
    setEstimateState("loading");
    const timer = window.setTimeout(() => {
      adminApi
        .broadcastEstimate(draft.audience, draft.targetValue || null)
        .then((value) => {
          if (cancelled) return;
          setEstimate(value.estimated_recipients);
          setEstimateState("idle");
        })
        .catch(() => {
          if (cancelled) return;
          setEstimate(null);
          setEstimateState("error");
        });
    }, ESTIMATE_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [draft.audience, draft.targetValue, targetReady]);

  const limit = draft.mediaType === "none" ? MAX_MESSAGE : MAX_CAPTION;
  const defaultBody = draft.bodies.uz.trim();
  const currentBody = draft.bodies[language];
  const previewBody = currentBody.trim() || defaultBody;
  const filledLanguages = LANGUAGES.filter((item) => draft.bodies[item.value].trim() !== "");
  const overLimit = LANGUAGES.filter((item) => draft.bodies[item.value].length > limit);

  const mediaReady = draft.mediaType === "none" || draft.mediaFileId.trim() !== "";
  const canCompose =
    defaultBody !== "" && targetReady && mediaReady && overLimit.length === 0;

  const update = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));

  const setBody = (value: string) =>
    setDraft((current) => ({
      ...current,
      bodies: { ...current.bodies, [language]: value },
    }));

  const send = async () => {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setError(null);

    // Built explicitly rather than spread from state, so a field can only
    // reach the API because someone wrote it here on purpose.
    const payload: BroadcastInput = {
      message: defaultBody,
      audience: draft.audience,
      media_type: draft.mediaType,
    };
    const translations: Partial<Record<BroadcastLanguage, string>> = {};
    for (const item of LANGUAGES) {
      const body = draft.bodies[item.value].trim();
      if (body) translations[item.value] = body;
    }
    if (Object.keys(translations).length > 0) payload.translations = translations;
    if (needsTarget(draft.audience)) payload.target_value = draft.targetValue;
    if (draft.mediaType !== "none") payload.media_file_id = draft.mediaFileId.trim();

    try {
      const created = await adminApi.sendBroadcast(payload);
      setDraft(EMPTY_DRAFT);
      setLanguage("uz");
      setConfirming(false);
      onSent(created);
    } catch (err) {
      setError(describeError(err));
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  if (confirming) {
    return (
      <div className="space-y-3">
        <SectionTitle>Tasdiqlash</SectionTitle>
        {error && <Notice message={error} tone="error" />}

        <div className="space-y-2 rounded-xl border border-danger/50 bg-surface p-3">
          <p className="flex items-center gap-2 font-body text-sm font-semibold text-ink">
            <AlertTriangle size={16} aria-hidden />
            Bu amalni bekor qilib bo'lmaydi
          </p>

          <dl className="space-y-1 font-body text-sm text-ink-dim">
            <SummaryRow label="Kimga">
              {AUDIENCES.find((item) => item.value === draft.audience)?.label}
              {needsTarget(draft.audience) &&
                ` → ${targetLabel(draft.audience, draft.targetValue)}`}
            </SummaryRow>
            <SummaryRow label="Taxminiy qabul qiluvchilar">
              {estimate === null ? "—" : estimate.toLocaleString("ru-RU")}
            </SummaryRow>
            <SummaryRow label="Turi">
              {MEDIA_TYPES.find((item) => item.value === draft.mediaType)?.label}
            </SummaryRow>
            <SummaryRow label="Tillar">
              {filledLanguages.map((item) => item.label).join(" · ")}
            </SummaryRow>
          </dl>

          <p className="font-body text-[11px] text-ink-dim">
            Yakuniy ro'yxat yuborish boshlanganda aniqlanadi.
          </p>
        </div>

        <BroadcastPreview
          language={language}
          body={previewBody}
          mediaType={draft.mediaType}
          isFallback={currentBody.trim() === "" && defaultBody !== ""}
        />

        <div className="grid grid-cols-2 gap-2">
          <Button tone="ghost" disabled={busy} onClick={() => setConfirming(false)}>
            Orqaga
          </Button>
          <Button tone="danger" disabled={busy} onClick={send}>
            {busy ? "Yuborilmoqda…" : "Yuborish"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <SectionTitle>Yangi xabar</SectionTitle>
      {error && <Notice message={error} tone="error" />}

      <Field label="Kimga">
        <div className="space-y-1.5">
          {AUDIENCES.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={draft.audience === item.value}
              onClick={() =>
                update({
                  audience: item.value,
                  // A target from a previous audience must not survive the
                  // switch — the backend refuses a stray one, and carrying
                  // it silently would look like a broken form.
                  targetValue: "",
                })
              }
              className={`flex w-full items-center justify-between rounded-lg border px-3 py-2.5 text-left font-body text-sm transition-colors ${
                draft.audience === item.value
                  ? "border-marquee bg-surface-hi text-ink"
                  : "border-surface-hi bg-surface text-ink-dim"
              }`}
            >
              <span>
                {item.label}
                <span className="block font-mono text-[10px] text-ink-dim">{item.hint}</span>
              </span>
            </button>
          ))}
        </div>
      </Field>

      {needsTarget(draft.audience) && (
        <Field label={draft.audience === "interest" ? "Qaysi qiziqish" : "Qaysi nishon"}>
          {targetOptions.length === 0 ? (
            <Notice message="Ro'yxat yuklanmadi. Sahifani yangilang." tone="error" />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {targetOptions.map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={draft.targetValue === value}
                  onClick={() => update({ targetValue: value })}
                  className={`rounded-full border px-3 py-1.5 font-body text-xs transition-colors ${
                    draft.targetValue === value
                      ? "border-marquee bg-surface-hi text-ink"
                      : "border-surface-hi bg-surface text-ink-dim"
                  }`}
                >
                  {targetLabel(draft.audience, value)}
                </button>
              ))}
            </div>
          )}
        </Field>
      )}

      <div className="rounded-xl border border-surface-hi bg-surface p-3">
        <p className="font-body text-sm text-ink">
          {estimateState === "loading" && "Hisoblanmoqda…"}
          {estimateState === "error" && "Hisoblab bo'lmadi."}
          {estimateState === "idle" &&
            (estimate === null
              ? "Kimga yuborilishini tanlang."
              : `${estimate.toLocaleString("ru-RU")} ta foydalanuvchi`)}
        </p>
        <p className="mt-0.5 font-body text-[11px] text-ink-dim">
          Taxminiy son. Yakuniy ro'yxat yuborish boshlanganda aniqlanadi.
        </p>
      </div>

      <Field label="Turi">
        <div className="grid grid-cols-3 gap-1.5">
          {MEDIA_TYPES.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={draft.mediaType === item.value}
              onClick={() => update({ mediaType: item.value, mediaFileId: "" })}
              className={`rounded-lg border py-2 font-body text-sm transition-colors ${
                draft.mediaType === item.value
                  ? "border-marquee bg-surface-hi text-ink"
                  : "border-surface-hi bg-surface text-ink-dim"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </Field>

      {draft.mediaType !== "none" && (
        <Field label="Media ID">
          <TextInput
            value={draft.mediaFileId}
            onChange={(value) => update({ mediaFileId: value })}
            placeholder="Botga forward qiling"
            mono
          />
          <p className="mt-1 font-body text-[11px] text-ink-dim">
            Faylni botga yuboring — bot ID qaytaradi. Fayl Telegramda qoladi.
          </p>
        </Field>
      )}

      <Field label="Matn">
        <div
          className="mb-1.5 grid grid-cols-3 gap-1.5"
          role="tablist"
          aria-label="Xabar tillari"
        >
          {LANGUAGES.map((item) => (
            <button
              key={item.value}
              type="button"
              role="tab"
              aria-selected={language === item.value}
              onClick={() => setLanguage(item.value)}
              className={`rounded-lg border py-1.5 font-body text-xs transition-colors ${
                language === item.value
                  ? "border-marquee bg-surface-hi text-ink"
                  : "border-surface-hi bg-surface text-ink-dim"
              }`}
            >
              {item.label}
              {draft.bodies[item.value].trim() !== "" && " ✓"}
            </button>
          ))}
        </div>

        <TextArea value={currentBody} onChange={setBody} rows={5} placeholder="Xabar…" />
        <p
          className={`mt-1 text-right font-mono text-[11px] ${
            currentBody.length > limit ? "text-danger" : "text-ink-dim"
          }`}
        >
          {currentBody.length} / {limit}
        </p>
        <p className="font-body text-[11px] text-ink-dim">
          Har bir foydalanuvchi o'z tilida oladi. O'zbekcha matn asosiy — boshqa til
          to'ldirilmasa, o'sha yuboriladi.
        </p>
      </Field>

      <BroadcastPreview
        language={language}
        body={previewBody}
        mediaType={draft.mediaType}
        isFallback={currentBody.trim() === "" && defaultBody !== ""}
      />

      <Button full disabled={!canCompose} onClick={() => setConfirming(true)}>
        Davom etish
      </Button>
      {defaultBody === "" && (
        <p className="font-body text-[11px] text-ink-dim">
          O'zbekcha matn majburiy — u asosiy matn hisoblanadi.
        </p>
      )}
    </div>
  );
}

function SummaryRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0">{label}</dt>
      <dd className="text-right font-medium text-ink">{children}</dd>
    </div>
  );
}

/**
 * Turns an API failure into something an operator can act on.
 *
 * Never renders a raw exception: the backend's 422 detail is a written
 * sentence and safe to show, while everything else gets a fixed message
 * so a stack trace or a connection string can never reach the screen.
 */
export function describeError(err: unknown): string {
  if (!(err instanceof ApiError)) return "Tarmoqda xatolik. Qayta urinib ko'ring.";
  switch (err.status) {
    case 401:
      return "Sessiya tugadi. Ilovani qayta oching.";
    case 403:
      return "Sizda bu amal uchun ruxsat yo'q.";
    case 404:
      return "Xabar topilmadi. Ro'yxat yangilandi.";
    case 409:
      return "Xabar holati o'zgardi. Ro'yxat yangilandi.";
    case 422:
      return err.message;
    case 429:
      return "Juda ko'p so'rov. Biroz kuting.";
    default:
      return "Serverda xatolik. Keyinroq urinib ko'ring.";
  }
}
