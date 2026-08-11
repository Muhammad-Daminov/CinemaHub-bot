export interface Movie {
  id: number;
  title: string;
  year: number | null;
  genres: string[] | null;
  poster_url: string | null;
  description: string | null;
  rating: number | null;
  view_count: number;
  /**
   * Episodes this title has; a film has 1. Any play control must check it
   * before starting playback — otherwise it silently starts episode 1 on a
   * serial, which is what the episode selector exists to prevent.
   */
  episode_count: number;
  /** Whether the current user has saved this title. */
  is_favorite: boolean;
  /**
   * The public code a viewer can type — in the bot chat or this app's
   * search box — to reach this title. Shown so the number is learnable.
   */
  code: string | null;
  /** Whether the title is subscribers-only. The same for every viewer. */
  is_premium: boolean;
  /**
   * Whether *this* viewer is locked out of it: premium, and they hold no
   * active subscription. A rendering hint only — the server re-decides on
   * every watch request, so forcing this to false in a debugger changes
   * the icon and nothing else.
   */
  is_locked: boolean;
}

/** Mirrors CollectionOut in app/api/movies.py. */
export interface MovieCollection {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  poster_url: string | null;
  title_count: number;
}

export type MovieContentType = "film" | "serial" | "multfilm" | "anime" | "drama";

/** Audio track languages a title can be restricted to. Mirrors AudioLanguage. */
export type AudioLanguageFilter = "uz_dub" | "uz_sub" | "ru" | "en" | "original";

export interface WatchResponse {
  status: string;
  message: string;
}

/** Mirrors EpisodeOut in app/api/movies.py. */
export interface Episode {
  id: number;
  season: number;
  number: number;
  name: string | null;
  duration_minutes: number | null;
  /** Audio tracks this episode actually has — per episode, not per title. */
  audio_languages: AudioLanguageFilter[];
  watched: boolean;
}

export interface EpisodePage {
  episodes: Episode[];
  page: number;
  has_more: boolean;
}

export type UserRole = "USER" | "MODERATOR" | "ADMIN" | "SUPER_ADMIN";

export interface UserProfile {
  telegram_id: number;
  username: string | null;
  full_name: string | null;
  balance: number;
  referral_code: string;
  is_premium: boolean;
  language: "uz" | "ru" | "en";
  language_selected: boolean;
  role: UserRole;
  is_admin: boolean;
  is_super_admin: boolean;
  /** Capability names the backend granted. Empty for ordinary users. */
  permissions: string[];
  /**
   * Reported, not enforced, by /auth/me — every other endpoint refuses a
   * banned user outright. The app renders a blocked notice on it so the
   * refusals do not look like an empty catalog.
   */
  is_banned: boolean;
}

/** Mirrors the billing API in app/api/billing.py. */
export interface BillingFeature {
  code: string;
  name: string;
  value: string | null;
}

export interface BillingPlan {
  id: number;
  code: string;
  name: string;
  description: string | null;
  price: number;
  duration_days: number;
  /** Tier rank. Higher wins; drives extend / upgrade / queue. */
  priority: number;
  benefits: string[];
  is_free: boolean;
  features: BillingFeature[];
}

export interface HeldSubscription {
  plan_id: number | null;
  plan_name: string | null;
  started_at: string;
  expires_at: string;
}

export interface BillingOverview {
  balance: number;
  plans: BillingPlan[];
  current: HeldSubscription | null;
  /** Lower-tier purchases waiting for the current term to end. */
  queued: HeldSubscription[];
}

export type PurchaseOutcome = "activate" | "extend" | "upgrade" | "queued";

export interface PurchasePreview {
  outcome: PurchaseOutcome;
  starts_at: string;
  price: number;
  balance: number;
  missing: number;
  affordable: boolean;
}

export interface PaymentCard {
  id: number;
  card_number: string;
  holder_name: string;
  bank_name: string | null;
}

export interface PaymentHistoryEntry {
  id: number;
  amount: number;
  kind: string;
  description: string | null;
  created_at: string;
  status: string | null;
}

/** Mirrors PaymentStatus in app/db/models/payment.py. */
export type PaymentStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "mismatch"
  | "cancelled";

/** Mirrors ReceiptStatusOut in app/api/billing.py — the caller's own payment. */
export interface ReceiptStatus {
  id: number;
  amount: number;
  status: PaymentStatus;
  created_at: string;
  reviewed_at: string | null;
  card_id: number | null;
  /** Present on a mismatch: what the reviewer read, so a retry prefills correctly. */
  verified_amount: number | null;
  reason: string | null;
  can_retry: boolean;
}

/** Mirrors BannerOut in app/api/movies.py — one hero slide, already
 *  resolved for the calling user by the backend. */
export interface BannerSlide {
  id: number;
  title_id: number | null;
  headline: string | null;
  subtitle: string | null;
  /** Locale key from a fixed allowlist, rendered in the viewer's language. */
  label_key: string | null;
  poster_url: string | null;
  personalized: boolean;
  /** Present when the slide points at a real title, so the carousel keeps
   *  its existing play/details behaviour. Null for upcoming promotions. */
  movie: Movie | null;
}

/** Mirrors ThemeOut in app/api/auth.py — the caller's resolved palette. */
export interface ResolvedTheme {
  key: string;
  name: string;
  /** CSS custom property name -> hex colour. */
  tokens: Record<string, string>;
  /** Allowlisted preset names, never raw CSS. */
  card_shape: string;
  decoration: string;
  /** Which precedence rule won, or null for the built-in default. */
  scope: string | null;
}
