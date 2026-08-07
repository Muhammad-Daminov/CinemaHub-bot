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
}
