// Mirrors the Pydantic response models in app/api/admin.py. String unions
// match the backend's str-Enum *values* (FastAPI serializes those, not names).

export type ContentType = "film" | "serial" | "multfilm" | "anime" | "drama";
export type AudioLanguage = "uz_dub" | "uz_sub" | "ru" | "en" | "original";
export type VideoQuality = "480p" | "720p" | "1080p" | "4k";
export type PaymentStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "mismatch"
  | "cancelled";
export type PaymentPurpose = "topup" | "subscription";
export type PromoDiscountType = "fixed_amount_balance" | "premium_days" | "percentage_discount";

export interface StatusResponse {
  status: string;
}

export interface AdminStats {
  total_users: number;
  premium_users: number;
  total_titles: number;
  total_episodes: number;
  titles_by_type: Record<string, number>;
  pending_receipts: number;
  pending_uploads: number;
  total_revenue: number;
  active_promo_codes: number;
}

export interface ActivityPoint {
  date: string;
  count: number;
}

export interface TopUser {
  telegram_id: number;
  username: string | null;
  balance: number;
}

export interface AdminTitle {
  id: number;
  content_type: ContentType;
  name: string;
  year: number | null;
  genres: string[] | null;
  country: string | null;
  tmdb_id: number | null;
  poster_url: string | null;
  description: string | null;
  rating: number | null;
  view_count: number;
  is_active: boolean;
  is_manual_override: boolean;
  created_at: string;
  /** Set when an admin uploaded a custom poster; overrides poster_url. */
  poster_image_id?: number | null;
  /** The public code viewers type to reach this title. Assigned on creation. */
  code?: string | null;
  /** Subscribers-only. */
  is_premium: boolean;
}

export interface AdminTitleListItem extends AdminTitle {
  episode_count: number;
  file_count: number;
}

export interface TitlePage {
  items: AdminTitleListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface TitleListParams {
  q?: string;
  content_type?: ContentType;
  is_active?: boolean;
  /** Omit for "all" — the server filters, so paging and `total` stay honest. */
  is_premium?: boolean;
  page?: number;
  page_size?: number;
}

export interface TitleInput {
  name: string;
  content_type: ContentType;
  year?: number | null;
  genres?: string[] | null;
  country?: string | null;
  description?: string | null;
  poster_url?: string | null;
  tmdb_id?: number | null;
  rating?: number | null;
  is_premium?: boolean;
}

export type TitleUpdateInput = Partial<TitleInput> & {
  is_active?: boolean;
  /** Reassigning a code 409s when it belongs to another title. */
  code?: string;
};

/** Mirrors TrialSettingsOut in app/api/admin.py. */
export interface TrialSettings {
  enabled: boolean;
  days: number;
}

export interface AdminEpisode {
  id: number;
  title_id: number;
  season: number;
  number: number;
  name: string | null;
  duration_minutes: number | null;
  view_count: number;
  file_count: number;
}

export interface EpisodeInput {
  season: number;
  number: number;
  name?: string | null;
  duration_minutes?: number | null;
}

export interface AdminMediaFile {
  id: number;
  episode_id: number;
  file_id: string;
  language: AudioLanguage;
  quality: VideoQuality;
  source_chat_id: number | null;
  source_message_id: number | null;
  created_at: string;
}

export interface MediaFileInput {
  file_id: string;
  language: AudioLanguage;
  quality: VideoQuality;
  source_chat_id?: number | null;
  source_message_id?: number | null;
}

export interface PendingUpload {
  id: number;
  file_id: string;
  uploaded_by_id: number | null;
  file_name: string | null;
  file_size: number | null;
  duration_seconds: number | null;
  created_at: string;
}

export interface PendingAttachInput {
  title_id?: number | null;
  name?: string | null;
  content_type?: ContentType | null;
  year?: number | null;
  season: number;
  number: number;
  language: AudioLanguage;
  quality: VideoQuality;
}

export interface AdminReceipt {
  id: number;
  telegram_id: number;
  username: string | null;
  full_name: string | null;
  purpose: PaymentPurpose;
  subscription_plan: string | null;
  amount: number;
  receipt_photo_file_id: string;
  status: PaymentStatus;
  card_id?: number | null;
  card_label?: string | null;
  verified_amount?: number | null;
  rejection_reason_id?: number | null;
  reviewed_at?: string | null;
  reviewer_telegram_id?: number | null;
  admin_notes: string | null;
  created_at: string;
}

export interface AdminCard {
  id: number;
  card_number: string;
  holder_name: string;
  bank_name: string | null;
  is_active: boolean;
}

export interface AdminCardInput {
  card_number: string;
  holder_name: string;
  bank_name?: string | null;
}

export interface AdminPromoCode {
  id: number;
  code: string;
  campaign_name: string | null;
  discount_type: PromoDiscountType;
  value: number;
  max_uses: number | null;
  current_uses: number;
  valid_until: string | null;
  is_active: boolean;
}

export interface PromoCodeInput {
  discount_type: PromoDiscountType;
  value: number;
  code?: string | null;
  max_uses?: number | null;
  valid_days?: number | null;
  campaign_name?: string | null;
}

export interface AdminUser {
  id: number;
  telegram_id: number;
  username: string | null;
  full_name: string | null;
  balance: number;
  is_premium: boolean;
  is_banned: boolean;
  created_at: string;
}

export interface UserPage {
  items: AdminUser[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserListParams {
  q?: string;
  page?: number;
  page_size?: number;
}

export interface AdminCollection {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  poster_url: string | null;
  sort_order: number;
  is_active: boolean;
  created_at: string;
  /** Set when an admin uploaded a custom poster; overrides poster_url. */
  poster_image_id?: number | null;
  /** The public code viewers type to reach this title. Assigned on creation. */
  code?: string | null;
  /** Subscribers-only. */
  is_premium: boolean;
}

export interface AdminCollectionListItem extends AdminCollection {
  title_count: number;
  /** Set when an admin uploaded a custom poster; overrides poster_url. */
  poster_image_id?: number | null;
  /** The public code viewers type to reach this title. Assigned on creation. */
  code?: string | null;
  /** Subscribers-only. */
  is_premium: boolean;
}

export interface CollectionInput {
  name: string;
  description?: string | null;
  poster_url?: string | null;
  sort_order?: number;
  slug?: string | null;
}

export type CollectionUpdateInput = Partial<CollectionInput> & { is_active?: boolean };

/** A possible duplicate surfaced while an admin types a new title name. */
export interface SimilarTitle {
  id: number;
  name: string;
  content_type: ContentType;
  year: number | null;
  poster_url: string | null;
  episode_count: number;
  languages: AudioLanguage[];
}

/** One TMDB search hit shown in the manual picker. Nothing is stored until tapped. */
export interface TMDBSearchResult {
  id: number;
  title: string;
  original_title: string | null;
  year: number | null;
  poster_url: string | null;
  overview: string | null;
}

/** Mirrors AdminOut in app/api/admin.py. */
export interface AdminAccount {
  id: number;
  telegram_id: number;
  username: string | null;
  full_name: string | null;
  role: string;
  is_super_admin: boolean;
  permissions: string[];
}

/**
 * The permission vocabulary, grouped for display. Served by the backend
 * so a capability added there shows up without a frontend release.
 */
export interface PermissionCatalog {
  groups: Record<string, string[]>;
}

/** Mirrors PlanFeatureOut in app/api/admin.py. */
export interface PlanFeatureGrant {
  id: number;
  code: string;
  name: string;
  description: string | null;
  /** Quantitative limit ("5" devices), or null for a plain on/off grant. */
  value: string | null;
}

/** Mirrors PlanOut. */
export interface SubscriptionPlan {
  id: number;
  code: string;
  name: string;
  description: string | null;
  price: number;
  duration_days: number;
  benefits: string[];
  is_active: boolean;
  is_free: boolean;
  sort_order: number;
  /** Why a delete may be refused — surfaced so the panel can say so up front. */
  subscriber_count: number;
  features: PlanFeatureGrant[];
}

export interface SubscriptionFeature {
  id: number;
  code: string;
  name: string;
  description: string | null;
  sort_order: number;
  is_active: boolean;
}

export interface PlanInput {
  code: string;
  name: string;
  price: number;
  duration_days: number;
  description?: string | null;
  benefits?: string[];
  is_active?: boolean;
  is_free?: boolean;
}

export interface PlanUpdateInput {
  name?: string;
  description?: string | null;
  price?: number;
  duration_days?: number;
  benefits?: string[];
  is_active?: boolean;
  is_free?: boolean;
}

/** Mirrors the broadcast API in app/api/admin.py. */
/**
 * Every audience the API can report. `interest` and `badge` are targeted:
 * they carry a `target_value` and are sized through the estimate route
 * rather than the audience-sizes list, so they are not composable from
 * this panel yet — but a broadcast created through the API can be one,
 * and the history has to render it honestly.
 */
export type BroadcastAudience = "all" | "premium" | "free" | "interest" | "badge";

/** The audiences this panel can currently compose. */
export type UntargetedAudience = Extract<BroadcastAudience, "all" | "premium" | "free">;

export type BroadcastStatus = "pending" | "sending" | "completed" | "failed";

/** Mirrors `BroadcastMedia`. Every value has a matching send path server-side. */
export type BroadcastMedia = "none" | "photo" | "video";

/** Interface languages a broadcast can carry a body in. */
export type BroadcastLanguage = "uz" | "ru" | "en";

/**
 * The target vocabulary, served by the backend from the same allowlists
 * that validate a create. Never hardcoded here — the panel must not be
 * able to offer a choice the API refuses.
 */
export interface BroadcastTargets {
  interests: string[];
  badges: string[];
  badge_families: string[];
}

export interface BroadcastEstimate {
  audience: BroadcastAudience;
  target_value: string | null;
  estimated_recipients: number;
}

/** The create payload. Deliberately has no field capable of naming a user. */
export interface BroadcastInput {
  message: string;
  translations?: Partial<Record<BroadcastLanguage, string>>;
  audience: BroadcastAudience;
  target_value?: string | null;
  media_type: BroadcastMedia;
  media_file_id?: string | null;
}

export interface AdminBroadcast {
  id: number;
  message: string;
  audience: BroadcastAudience;
  /** What a targeted send was addressed at; null for the untargeted ones. */
  target_value: string | null;
  /**
   * Which kind of media this carries. The `file_id` itself is deliberately
   * absent from every response — the panel needs to know a broadcast has a
   * photo, not which one.
   */
  media_type: BroadcastMedia;
  status: BroadcastStatus;
  total_recipients: number;
  sent_count: number;
  /** Users who blocked the bot — churn, not a delivery fault. */
  blocked_count: number;
  failed_count: number;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

/**
 * One broadcast plus its live delivery breakdown, counted server-side from
 * the recipient rows. `can_resume` is the server's decision; the panel
 * renders the button but never decides whether one is warranted.
 */
export interface AdminBroadcastDetail extends AdminBroadcast {
  pending: number;
  sending: number;
  sent: number;
  failed: number;
  skipped: number;
  can_resume: boolean;
  languages: BroadcastLanguage[];
}

export interface BroadcastAudienceSize {
  audience: BroadcastAudience;
  size: number;
}

export interface MembershipSettings {
  require_membership: boolean;
  required_channel: string | null;
  /** False for a numeric chat id: the check works, but no join link exists. */
  has_invite_url: boolean;
}

/** Mirrors TitleTranslationOut in app/api/admin.py. */
export type TranslationLanguage = "uz" | "ru" | "en";

export type TranslationSource = "manual" | "tmdb";

export interface AdminTitleTranslation {
  language: TranslationLanguage;
  name: string;
  description: string | null;
  /** "tmdb" rows are auto-filled and may be overwritten by a later fill;
   *  "manual" ones never are. */
  source: TranslationSource;
}

export interface TitleTranslationInput {
  language: TranslationLanguage;
  /** Empty removes the translation for that language. */
  name: string;
  description: string | null;
}

/** Mirrors RejectionReasonOut. Built-ins have a `code` and are localized
 *  server-side; admin-authored ones carry `label` verbatim. */
export interface AdminRejectionReason {
  id: number;
  code: string | null;
  label: string | null;
  sort_order: number;
}

/** Mirrors ThemeAdminOut in app/api/admin.py. */
export interface AdminTheme {
  id: number;
  key: string;
  name: string;
  description: string | null;
  is_default: boolean;
  is_active: boolean;
  tokens: Record<string, string>;
  card_shape: string;
  decoration: string;
  /** Advisory readability problems — never blocking. */
  contrast_warnings: {
    foreground: string;
    background: string;
    label: string;
    ratio: number;
    required: number;
  }[];
}

export interface ThemeVocabulary {
  defaults: Record<string, string>;
  card_shapes: Record<string, string>;
  decorations: string[];
}

export interface ThemeInput {
  key: string;
  name: string;
  description?: string | null;
  tokens?: Record<string, string>;
  card_shape?: string | null;
  decoration?: string | null;
}

export type ThemeScope = "user" | "badge" | "interest" | "subscription" | "global";

export interface AdminThemeAssignment {
  id: number;
  theme_id: number;
  scope: ThemeScope;
  user_id: number | null;
  target_value: string | null;
  priority: number;
  is_active: boolean;
}

export interface ThemeAssignmentInput {
  theme_id: number;
  scope: ThemeScope;
  /**
   * For a `user` assignment, omitting this targets the authenticated
   * administrator — the server resolves it from the verified session, so
   * the "apply to my own panel" action sends no id at all.
   */
  user_id?: number | null;
  target_value?: string | null;
  priority?: number;
}

/** Mirrors BannerAdminOut. `title_id` is nullable so a "coming soon"
 *  announcement needs no catalog entry. */
export interface AdminBanner {
  id: number;
  title_id: number | null;
  headline: string | null;
  subtitle: string | null;
  label_key: string | null;
  image_url: string | null;
  audience: "global" | "content_type" | "badge" | "premium" | "free";
  target_value: string | null;
  priority: number;
  is_active: boolean;
  starts_at: string | null;
  ends_at: string | null;
}

export type BannerInput = Partial<Omit<AdminBanner, "id">>;
