export interface Movie {
  id: number;
  title: string;
  year: number | null;
  genres: string[] | null;
  poster_url: string | null;
  description: string | null;
  rating: number | null;
  view_count: number;
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

export interface UserProfile {
  telegram_id: number;
  username: string | null;
  full_name: string | null;
  balance: number;
  referral_code: string;
  is_premium: boolean;
  language: "uz" | "ru" | "en";
  language_selected: boolean;
}
