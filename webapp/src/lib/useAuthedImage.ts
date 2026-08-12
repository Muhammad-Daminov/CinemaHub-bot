import { useEffect, useRef, useState } from "react";
import { fetchImageObjectUrl } from "./api";

/**
 * Resolves an image URL into something an `<img>` can actually display.
 *
 * A poster is one of two very different things. TMDB's is a public URL and
 * works in a plain `src`. An admin-uploaded one is served by
 * `/api/movies/images/{id}`, which sits behind `get_active_user` and needs
 * the `X-Telegram-Init-Data` header — and an `<img>` cannot send headers,
 * so pointing `src` at it always failed. Every uploaded poster therefore
 * rendered as a broken image everywhere it appeared.
 *
 * The fix is to fetch the private ones with the header and hand back a
 * blob URL. The endpoint stays authenticated: making poster bytes public
 * would fix the symptom by removing the check, and whether that is
 * acceptable is a security decision recorded as open in TASKS.md P2-16.
 *
 * Public URLs are returned untouched — no fetch, no blob, no behaviour
 * change for the overwhelming majority of the catalog, which is TMDB
 * artwork.
 *
 * Two things this has to get right, both of which bit the first version
 * written inline in PosterPicker:
 *
 *   - **Revoking.** An object URL lives until revoked; a scrolling
 *     catalog that creates one per card and never frees them leaks for as
 *     long as the app is open.
 *   - **Staleness.** The fetch is async and the component's `src` can
 *     change under it. Without the guard below, a slow response for the
 *     previous poster can land after a newer one and overwrite it — the
 *     card would show a film the user has already scrolled past.
 */
export function useAuthedImage(
  src: string | null | undefined,
  options?: { onError?: (error: unknown) => void },
): string | null {
  // Seeded with the public value so a TMDB poster paints on the first
  // render rather than flashing empty for an effect that will not change it.
  const [resolved, setResolved] = useState<string | null>(
    src && !isPrivate(src) ? src : null,
  );
  // Held in a ref, not in the effect closure. The previous version revoked
  // from the effect's cleanup, which runs *before* the replacement value
  // exists — so `resolved` went on pointing at a URL that had already been
  // revoked, and the browser drew a broken-image icon for it. Revoking is
  // now tied to the state moving off the URL rather than to the effect
  // being torn down.
  const held = useRef<string | null>(null);
  const onError = options?.onError;

  useEffect(() => {
    const release = () => {
      if (held.current) {
        URL.revokeObjectURL(held.current);
        held.current = null;
      }
    };

    if (!src) {
      setResolved(null);
      release();
      return;
    }
    if (!isPrivate(src)) {
      setResolved(src);
      release();
      return;
    }

    // Both in the same task, before the browser can paint again: the state
    // stops pointing at the old blob and only then is it freed, so there is
    // no frame in which an <img> holds a revoked URL.
    setResolved(null);
    release();

    let cancelled = false;
    fetchImageObjectUrl(src)
      .then((url) => {
        if (cancelled) {
          // Arrived after this effect was superseded: adopting it now would
          // both leak the URL and show an image the caller moved on from.
          URL.revokeObjectURL(url);
          return;
        }
        held.current = url;
        setResolved(url);
      })
      .catch((error) => {
        if (cancelled) return;
        setResolved(null);
        // Reported rather than swallowed. A poster that will not load is a
        // fallback for a viewer, but for the administrator who just
        // uploaded it, silence is the difference between "no poster" and
        // "your upload is broken".
        onError?.(error);
      });

    return () => {
      cancelled = true;
    };
  }, [src, onError]);

  // The last owner of the URL is the unmount, since nothing will render it
  // again afterwards.
  useEffect(
    () => () => {
      if (held.current) URL.revokeObjectURL(held.current);
    },
    [],
  );

  return resolved;
}


/**
 * Whether this URL needs the init-data header.
 *
 * Same-origin `/api/...` paths are ours and authenticated. Everything else
 * — TMDB's https URLs, and the `blob:`/`data:` URLs a local file preview
 * produces — is left exactly as it was.
 */
function isPrivate(src: string): boolean {
  return src.startsWith("/api/");
}
