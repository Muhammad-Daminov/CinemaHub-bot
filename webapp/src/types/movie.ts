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
}
