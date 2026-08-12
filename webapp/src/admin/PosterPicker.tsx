/**
 * Poster source picker: TMDB's, or one from the device gallery.
 *
 * Upload is a first-class option rather than a workaround — TMDB has no
 * artwork at all for a good deal of regional content, which is exactly
 * the catalog this platform carries.
 *
 * The preview shows what is *currently in effect*, resolving the same way
 * the viewer API does: an uploaded poster wins, TMDB's is the fallback.
 * Clearing the upload therefore reverts rather than blanking the poster,
 * which is why "remove" is safe to offer without a confirmation.
 */
import { ImageUp, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";
import { useAuthedImage } from "../lib/useAuthedImage";
import { Button, Notice } from "./ui";

interface Props {
  /** Currently effective poster, already resolved by the backend. */
  currentUrl: string | null;
  /** TMDB's URL, shown as the fallback once a custom poster is removed. */
  fallbackUrl?: string | null;
  hasCustom: boolean;
  onUpload: (file: File) => Promise<unknown>;
  onClear: () => Promise<unknown>;
  onChanged: () => void;
  disabled?: boolean;
}

/**
 * Deliberately `image/*` rather than a list of three MIME types.
 *
 * The narrow list was the reason a gallery pick failed on a phone: this
 * panel runs inside a Telegram WebView, and a picker filtered to exactly
 * jpeg/png/webp hides or greys out everything it cannot label — an
 * iPhone's HEIC photos, and the many Android gallery entries reported as
 * `application/octet-stream`. The admin could not select their photo at
 * all, so nothing was ever uploaded and there was no error to read.
 *
 * Widening this loses no safety, because the accept attribute was never
 * the check. `store_image` treats the declared type as an unverified
 * claim and decides by decoding the bytes with Pillow, then re-encodes
 * everything to JPEG or PNG — which is also what neutralises a disguised
 * file. Anything undecodable is refused there with a message about the
 * file, and the 8MB limit is enforced server-side too.
 */
const ACCEPT = "image/*";

export function PosterPicker({
  currentUrl,
  fallbackUrl,
  hasCustom,
  onUpload,
  onClear,
  onChanged,
  disabled,
}: Props) {
  const [pending, setPending] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The stored poster, resolved to something an <img> can actually show:
  // an uploaded one is behind the authenticated image endpoint, a TMDB URL
  // is used as-is. Shared with the catalog rather than duplicated here.
  // The failure is reported, not swallowed: an administrator who has just
  // uploaded a poster needs to know the difference between "there is no
  // poster" and "the poster cannot be loaded".
  const onImageError = useCallback(
    () => setError("Saqlangan posterni ko'rsatib bo'lmadi."),
    [],
  );
  const storedUrl = useAuthedImage(currentUrl, { onError: onImageError });
  const input = useRef<HTMLInputElement | null>(null);

  // Object URLs leak until revoked, and replacing the file makes a new one.
  useEffect(() => {
    if (!pending) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(pending);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [pending]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      setPending(null);
      setError(null);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yuklab bo'lmadi.");
    } finally {
      setBusy(false);
    }
  };

  const shown = previewUrl ?? storedUrl;

  return (
    <div className="space-y-2">
      {error && <Notice message={error} tone="error" />}

      <div className="flex gap-3">
        <div className="h-32 w-22 shrink-0 overflow-hidden rounded-lg border border-surface-hi bg-bg">
          {shown ? (
            <img src={shown} alt="" className="h-full w-full object-cover" />
          ) : (
            <div className="flex h-full items-center justify-center font-body text-[10px] text-ink-dim">
              Poster yo'q
            </div>
          )}
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              setPending(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />

          {pending ? (
            <>
              {/* Preview before saving — the point of choosing on a phone
                  is seeing it at poster size before committing. */}
              <Button disabled={busy || disabled} onClick={() => void run(() => onUpload(pending))}>
                <ImageUp size={13} /> Saqlash
              </Button>
              <Button tone="ghost" disabled={busy} onClick={() => setPending(null)}>
                Bekor qilish
              </Button>
            </>
          ) : (
            <Button tone="ghost" disabled={busy || disabled} onClick={() => input.current?.click()}>
              <RefreshCw size={13} /> {hasCustom ? "Almashtirish" : "Galereyadan yuklash"}
            </Button>
          )}

          {hasCustom && !pending && (
            <Button tone="danger" disabled={busy} onClick={() => void run(onClear)}>
              <Trash2 size={13} /> TMDB'ga qaytarish
            </Button>
          )}

          <p className="font-body text-[10px] leading-tight text-ink-dim">
            Rasm tanlang — avtomatik siqiladi (8MB gacha).
            {hasCustom && fallbackUrl ? " O'chirilsa TMDB posteri qaytadi." : ""}
          </p>
        </div>
      </div>
    </div>
  );
}
