/**
 * The optional decorative layer behind the app.
 *
 * Every decoration is a **compiled React component** drawn from a fixed
 * set — not an uploaded file, not a URL, not a string of SVG. There is
 * nothing here for an admin to inject into: the backend stores a name
 * from an allowlist, and this module maps that name to markup it already
 * contains. An unknown name renders nothing.
 *
 * Purely presentational and inert: fixed, behind everything, and
 * `pointer-events-none`, so it can never intercept a tap. Colours come
 * from theme tokens, so a decoration inherits whatever palette is active
 * rather than carrying colours of its own.
 *
 * "none" is the default and renders nothing at all — a theme without
 * decoration looks exactly like the app does today.
 */
import { useAppearance } from "./ThemeProvider";

function Stars() {
  // Deterministic positions rather than random ones: a layer that
  // reshuffled on every render would shimmer during navigation.
  const stars = [
    [8, 12], [22, 40], [37, 18], [51, 63], [64, 27], [78, 52], [90, 15],
    [14, 72], [29, 88], [45, 34], [58, 91], [72, 78], [85, 66], [95, 45],
  ];
  return (
    <svg className="h-full w-full" aria-hidden focusable="false">
      {stars.map(([x, y], index) => (
        <circle
          key={index}
          cx={`${x}%`}
          cy={`${y}%`}
          r={index % 3 === 0 ? 1.6 : 1}
          fill="var(--color-marquee)"
        />
      ))}
    </svg>
  );
}

function Cinema() {
  /* Sprocket holes down both edges, like a film strip. */
  const holes = [6, 18, 30, 42, 54, 66, 78, 90];
  return (
    <svg className="h-full w-full" aria-hidden focusable="false">
      {holes.map((y) => (
        <g key={y}>
          <rect x="1%" y={`${y}%`} width="10" height="14" rx="2" fill="var(--color-ink-dim)" />
          <rect x="96%" y={`${y}%`} width="10" height="14" rx="2" fill="var(--color-ink-dim)" />
        </g>
      ))}
    </svg>
  );
}

function Anime() {
  /* Speed lines radiating from the top corner. */
  return (
    <svg className="h-full w-full" aria-hidden focusable="false">
      {Array.from({ length: 12 }, (_, index) => (
        <line
          key={index}
          x1="100%"
          y1="0"
          x2={`${100 - index * 9}%`}
          y2={`${index * 9}%`}
          stroke="var(--color-marquee)"
          strokeWidth="1"
        />
      ))}
    </svg>
  );
}

function Horror() {
  /* Uneven vertical streaks, dark and sparse. */
  const streaks = [11, 27, 39, 58, 71, 84, 93];
  return (
    <svg className="h-full w-full" aria-hidden focusable="false">
      {streaks.map((x, index) => (
        <rect
          key={x}
          x={`${x}%`}
          y="0"
          width={index % 2 ? 2 : 1}
          height={`${40 + index * 7}%`}
          fill="var(--color-premiere)"
        />
      ))}
    </svg>
  );
}

function Abstract() {
  return (
    <svg className="h-full w-full" aria-hidden focusable="false">
      <circle cx="15%" cy="20%" r="70" fill="var(--color-marquee)" />
      <circle cx="85%" cy="70%" r="110" fill="var(--color-premiere)" />
      <circle cx="50%" cy="45%" r="50" fill="var(--color-marquee-dim)" />
    </svg>
  );
}

function Seasonal() {
  /* Soft falling flakes. */
  const flakes = [
    [10, 15], [25, 55], [40, 25], [55, 75], [70, 35], [85, 65], [95, 20],
    [18, 85], [33, 45], [48, 95],
  ];
  return (
    <svg className="h-full w-full" aria-hidden focusable="false">
      {flakes.map(([x, y], index) => (
        <circle key={index} cx={`${x}%`} cy={`${y}%`} r={index % 2 ? 2.5 : 1.8} fill="var(--color-ink)" />
      ))}
    </svg>
  );
}

/** The only decorations that exist. A name outside this map draws nothing. */
const DECORATIONS: Record<string, () => JSX.Element> = {
  stars: Stars,
  cinema: Cinema,
  anime: Anime,
  horror: Horror,
  abstract: Abstract,
  seasonal: Seasonal,
};

/**
 * Every selectable key, "none" first — the same vocabulary the server
 * validates against. Exported so the admin picker offers exactly what the
 * API accepts and cannot drift into showing an option that would be
 * rejected on save.
 */
export const DECORATION_KEYS = ["none", ...Object.keys(DECORATIONS)] as const;

/**
 * Just the artwork, with no positioning of its own.
 *
 * Split out so a thumbnail and the real background layer are the *same*
 * compiled component rendered at two sizes — what an admin previews is
 * therefore what a viewer gets, rather than a hand-drawn approximation
 * that could quietly diverge from it.
 */
export function DecorationArt({ name }: { name: string }) {
  const Decoration = DECORATIONS[name];
  if (!Decoration) return null;
  return <Decoration />;
}

export function DecorationLayer({ decoration }: { decoration?: string }) {
  const appearance = useAppearance();
  const name = decoration ?? appearance.decoration;
  if (!DECORATIONS[name]) return null;

  return (
    <div
      aria-hidden
      // Behind the app and inert. Low opacity so it decorates rather than
      // competing with content — legibility is not negotiable.
      className="pointer-events-none fixed inset-0 -z-10 opacity-[0.07]"
    >
      <DecorationArt name={name} />
    </div>
  );
}
