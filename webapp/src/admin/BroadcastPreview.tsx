/**
 * What one recipient will see, rendered in isolation.
 *
 * **Isolated in the strict sense**: this component reads nothing global
 * and writes nothing global. It does not touch `document.documentElement`,
 * does not call `setProperty`, does not read or write `localStorage`, does
 * not mount a real banner, does not start a timer, and cannot cause a
 * send. It is a pure function of its props — which is what makes it safe
 * to render beside the live admin panel without the two interfering.
 *
 * Everything is rendered as **text**. No `dangerouslySetInnerHTML`, no
 * markdown-to-HTML, no injected style strings. The message an admin types
 * is displayed with `whiteSpace: pre-wrap` so their line breaks survive,
 * and nothing else about it is interpreted.
 *
 * The media block is a **labelled placeholder, not the real image**. The
 * bytes live on Telegram's servers behind a bot token; showing them would
 * mean proxying media through our backend for the sake of a preview,
 * which is a new attack surface bought for a thumbnail. The preview says
 * what kind of media is attached and where it sits relative to the
 * caption, which is what the composing admin actually needs to judge.
 */
import { Image, Video } from "lucide-react";
import type { BroadcastLanguage, BroadcastMedia } from "../types/admin";

const LANGUAGE_LABELS: Record<BroadcastLanguage, string> = {
  uz: "O'zbek",
  ru: "Русский",
  en: "English",
};

interface Props {
  language: BroadcastLanguage;
  /** Already resolved by the caller, including the fallback to the default body. */
  body: string;
  mediaType: BroadcastMedia;
  /** True when this language had no body of its own and fell back. */
  isFallback: boolean;
}

export function BroadcastPreview({ language, body, mediaType, isFallback }: Props) {
  return (
    <div className="rounded-xl border border-surface-hi bg-surface p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-wide text-ink-dim">
          Ko'rinishi
        </span>
        <span className="font-mono text-[10px] text-ink-dim">
          {LANGUAGE_LABELS[language]}
        </span>
      </div>

      {/* A chat bubble, not a Telegram replica — close enough to judge
          length and line breaks, honest about not being the real client. */}
      <div className="rounded-2xl rounded-tl-sm bg-surface-hi p-3">
        {mediaType !== "none" && (
          <div
            className="mb-2 flex items-center justify-center gap-2 rounded-lg border border-dashed border-ink-dim/40 py-6 text-ink-dim"
            role="img"
            aria-label={mediaType === "photo" ? "Rasm biriktirilgan" : "Video biriktirilgan"}
          >
            {mediaType === "photo" ? <Image size={18} /> : <Video size={18} />}
            <span className="font-body text-xs">
              {mediaType === "photo" ? "Rasm" : "Video"}
            </span>
          </div>
        )}

        {body.trim() ? (
          <p
            className="font-body text-sm text-ink"
            // Preserves the admin's line breaks without interpreting a
            // single other thing about their text.
            style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
          >
            {body}
          </p>
        ) : (
          <p className="font-body text-sm italic text-ink-dim">Matn kiritilmagan</p>
        )}
      </div>

      {isFallback && (
        <p className="mt-2 font-body text-[11px] text-ink-dim">
          Bu til uchun alohida matn yo'q — asosiy matn yuboriladi.
        </p>
      )}
    </div>
  );
}
