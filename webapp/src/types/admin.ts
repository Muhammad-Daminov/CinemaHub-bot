// Mirrors the Pydantic response models in app/api/admin.py. String unions
// match the backend's str-Enum *values* (FastAPI serializes those, not names).

export type ContentType = "film" | "serial" | "multfilm" | "anime" | "drama";
export type AudioLanguage = "uz_dub" | "uz_sub" | "ru" | "en" | "original";
export type VideoQuality = "480p" | "720p" | "1080p" | "4k";
export type PaymentStatus = "pending" | "approved" | "rejected";
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
}

export type TitleUpdateInput = Partial<TitleInput> & { is_active?: boolean };

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
}

export interface AdminCollectionListItem extends AdminCollection {
  title_count: number;
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
