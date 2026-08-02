/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Each resolves via a CSS variable flipped by the .dark class on <html>
        // (see src/index.css) — components use one class name for both themes.
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        "surface-hi": "var(--color-surface-hi)",
        ink: "var(--color-ink)",
        "ink-dim": "var(--color-ink-dim)",
        // Marquee gold — the film-reel/marquee-light accent, deliberately not
        // Netflix red or a generic AI-vermilion/acid-green default.
        marquee: { DEFAULT: "var(--color-marquee)", dim: "var(--color-marquee-dim)" },
        // Reserved for Premium badges / live indicators only — used sparingly.
        premiere: "var(--color-premiere)",
        // Fixed (not theme-variable) contrast colors for text placed directly
        // on the marquee/premiere accent backgrounds — those two backgrounds
        // don't flip with theme, so their foreground text shouldn't either.
        "on-marquee": "#0A0A0D",
        "on-premiere": "#F5F5F7",
      },
      fontFamily: {
        display: ["Oswald", "sans-serif"],
        body: ["Inter", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      boxShadow: {
        marquee: "0 0 24px 2px rgba(232, 184, 75, 0.35)",
      },
    },
  },
  plugins: [],
};
