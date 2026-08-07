/**
 * Small shared primitives for the admin panels.
 *
 * The admin UI is form-heavy in a way the viewer-facing app is not, so
 * these keep field/button styling in one place instead of repeating the
 * same Tailwind strings across eight panels. Everything here uses the
 * project's colour tokens only — no raw hex.
 */
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import type { AudioLanguage, ContentType, VideoQuality } from "../types/admin";

export const CONTENT_TYPES: { value: ContentType; label: string }[] = [
  { value: "film", label: "Kino" },
  { value: "serial", label: "Serial" },
  { value: "multfilm", label: "Multfilm" },
  { value: "anime", label: "Anime" },
  { value: "drama", label: "Drama" },
];

export const AUDIO_LANGUAGES: { value: AudioLanguage; label: string }[] = [
  { value: "uz_dub", label: "O'zbek dublyaj" },
  { value: "uz_sub", label: "O'zbek subtitr" },
  { value: "ru", label: "Rus tilida" },
  { value: "en", label: "Ingliz tilida" },
  { value: "original", label: "Original" },
];

export const VIDEO_QUALITIES: { value: VideoQuality; label: string }[] = [
  { value: "480p", label: "480p" },
  { value: "720p", label: "720p" },
  { value: "1080p", label: "1080p" },
  { value: "4k", label: "4K" },
];

export function contentTypeLabel(value: string): string {
  return CONTENT_TYPES.find((item) => item.value === value)?.label ?? value;
}

export function languageLabel(value: string): string {
  return AUDIO_LANGUAGES.find((item) => item.value === value)?.label ?? value;
}

const FIELD_CLASS =
  "w-full rounded-lg border border-surface-hi bg-surface px-3 py-2 font-body text-sm text-ink " +
  "placeholder:text-ink-dim focus:border-marquee focus:outline-none";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block font-body text-xs text-ink-dim">{label}</span>
      {children}
    </label>
  );
}

interface TextInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: "text" | "number";
  mono?: boolean;
}

export function TextInput({ value, onChange, placeholder, type = "text", mono }: TextInputProps) {
  return (
    <input
      type={type}
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className={`${FIELD_CLASS} ${mono ? "font-mono" : ""}`}
    />
  );
}

export function TextArea({
  value,
  onChange,
  placeholder,
  rows = 3,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
}) {
  return (
    <textarea
      value={value}
      rows={rows}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className={`${FIELD_CLASS} resize-none`}
    />
  );
}

export function Select<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
        // appearance-none is the actual fix: bg-surface was already on this
        // element, but a native select paints the UA widget over it, and that
        // widget is white regardless of the classes around it. pr-9 leaves
        // room for the chevron we now have to draw ourselves.
        className={`${FIELD_CLASS} appearance-none pr-9`}
      >
        {options.map((option) => (
          // The popup list is rendered by the OS, not inside the page, so it
          // inherits nothing from the control. These classes cover the
          // browsers that do consult them; index.css sets color-scheme for
          // the ones that only follow that.
          <option key={option.value} value={option.value} className="bg-surface text-ink">
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        size={15}
        aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-dim"
      />
    </div>
  );
}

type ButtonTone = "primary" | "ghost" | "danger";

const TONE_CLASS: Record<ButtonTone, string> = {
  primary: "bg-marquee text-on-marquee",
  ghost: "border border-surface-hi bg-surface text-ink",
  danger: "bg-premiere text-on-premiere",
};

export function Button({
  children,
  onClick,
  tone = "primary",
  disabled,
  full,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: ButtonTone;
  disabled?: boolean;
  full?: boolean;
  /** Native tooltip — the only affordance a disabled button has to say why. */
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`rounded-lg px-3 py-2 font-body text-sm font-medium transition-opacity disabled:opacity-50 ${
        TONE_CLASS[tone]
      } ${full ? "w-full" : ""}`}
    >
      {children}
    </button>
  );
}

/** Compact icon-sized action, used inside dense list rows. */
export function IconButton({
  children,
  onClick,
  label,
  tone = "ghost",
}: {
  children: ReactNode;
  onClick: () => void;
  label: string;
  tone?: ButtonTone;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      className={`rounded-lg p-2 transition-opacity ${TONE_CLASS[tone]}`}
    >
      {children}
    </button>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 font-display text-sm font-medium uppercase tracking-wide text-ink-dim">
      {children}
    </h3>
  );
}

export function CardShell({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-surface-hi bg-surface p-3">{children}</div>;
}

export function EmptyState({ message }: { message: string }) {
  return <p className="py-10 text-center font-body text-sm text-ink-dim">{message}</p>;
}

export function Notice({ message, tone = "info" }: { message: string; tone?: "info" | "error" }) {
  return (
    <p
      className={`rounded-lg px-3 py-2 font-body text-xs ${
        tone === "error" ? "bg-premiere text-on-premiere" : "bg-surface-hi text-ink-dim"
      }`}
    >
      {message}
    </p>
  );
}

export function Badge({ children, active }: { children: ReactNode; active?: boolean }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 font-body text-[11px] ${
        active ? "bg-marquee text-on-marquee" : "bg-surface-hi text-ink-dim"
      }`}
    >
      {children}
    </span>
  );
}

export function formatMoney(value: number): string {
  return value.toLocaleString("en-US");
}

export function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}
