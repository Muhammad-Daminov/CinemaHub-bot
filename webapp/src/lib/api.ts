import { getInitData } from "./telegram";
import type { Movie, UserProfile, WatchResponse } from "../types/movie";
import type {
  ActivityPoint,
  AdminCard,
  AdminCardInput,
  AdminEpisode,
  AdminMediaFile,
  AdminPromoCode,
  AdminReceipt,
  AdminStats,
  AdminTitle,
  EpisodeInput,
  MediaFileInput,
  PaymentStatus,
  PendingAttachInput,
  PendingUpload,
  PromoCodeInput,
  StatusResponse,
  TitleInput,
  TitleListParams,
  TitlePage,
  TitleUpdateInput,
  TopUser,
  UserListParams,
  UserPage,
} from "../types/admin";

const API_BASE = "/api";

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": getInitData(),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail ?? "Request failed");
  }
  return response.json() as Promise<T>;
}

export const api = {
  me: () => request<UserProfile>("/auth/me"),
  listMovies: (params: { skip?: number; limit?: number; genre?: string } = {}) => {
    const query = new URLSearchParams(params as Record<string, string>).toString();
    return request<Movie[]>(`/movies${query ? `?${query}` : ""}`);
  },
  topMovies: (limit = 10) => request<Movie[]>(`/movies/top?limit=${limit}`),
  searchMovies: (q: string) => request<Movie[]>(`/movies/search?q=${encodeURIComponent(q)}`),
  watchMovie: (movieId: number) =>
    request<WatchResponse>(`/movies/${movieId}/watch`, { method: "POST" }),
};

/** Serializes only the params that were actually set — `false` and `0` are kept. */
function toQuery(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

const send = <T>(path: string, method: string, body?: unknown) =>
  request<T>(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

export const adminApi = {
  // ---------- dashboard ----------
  stats: () => request<AdminStats>("/admin/stats"),
  activity: () => request<ActivityPoint[]>("/admin/activity"),
  topUsers: (limit = 5) => request<TopUser[]>(`/admin/top-users?limit=${limit}`),

  // ---------- titles ----------
  listTitles: (params: TitleListParams = {}) => request<TitlePage>(`/admin/titles${toQuery(params)}`),
  createTitle: (body: TitleInput) => send<AdminTitle>("/admin/titles", "POST", body),
  updateTitle: (id: number, body: TitleUpdateInput) =>
    send<AdminTitle>(`/admin/titles/${id}`, "PATCH", body),
  toggleTitle: (id: number) => send<AdminTitle>(`/admin/titles/${id}/toggle`, "PATCH"),
  deleteTitle: (id: number) => send<StatusResponse>(`/admin/titles/${id}`, "DELETE"),
  enrichTitle: (id: number) => send<AdminTitle>(`/admin/titles/${id}/enrich`, "POST"),

  // ---------- episodes & files ----------
  listEpisodes: (titleId: number) => request<AdminEpisode[]>(`/admin/titles/${titleId}/episodes`),
  createEpisode: (titleId: number, body: EpisodeInput) =>
    send<AdminEpisode>(`/admin/titles/${titleId}/episodes`, "POST", body),
  deleteEpisode: (id: number) => send<StatusResponse>(`/admin/episodes/${id}`, "DELETE"),
  // NOTE: the backend exposes no GET for this path yet — see TitleEditor.
  listEpisodeFiles: (episodeId: number) =>
    request<AdminMediaFile[]>(`/admin/episodes/${episodeId}/files`),
  attachFile: (episodeId: number, body: MediaFileInput) =>
    send<AdminMediaFile>(`/admin/episodes/${episodeId}/files`, "POST", body),
  deleteFile: (id: number) => send<StatusResponse>(`/admin/files/${id}`, "DELETE"),

  // ---------- pending uploads ----------
  pendingUploads: () => request<PendingUpload[]>("/admin/pending-uploads"),
  attachPendingUpload: (id: number, body: PendingAttachInput) =>
    send<AdminMediaFile>(`/admin/pending-uploads/${id}/attach`, "POST", body),
  deletePendingUpload: (id: number) => send<StatusResponse>(`/admin/pending-uploads/${id}`, "DELETE"),

  // ---------- users ----------
  listUsers: (params: UserListParams = {}) => request<UserPage>(`/admin/users${toQuery(params)}`),

  // ---------- receipts ----------
  listReceipts: (status: PaymentStatus = "pending") =>
    request<AdminReceipt[]>(`/admin/receipts?status=${status}`),
  approveReceipt: (id: number) => send<StatusResponse>(`/admin/receipts/${id}/approve`, "POST"),
  rejectReceipt: (id: number, notes: string) =>
    send<StatusResponse>(`/admin/receipts/${id}/reject`, "POST", { notes }),

  // ---------- cards ----------
  listCards: () => request<AdminCard[]>("/admin/cards"),
  createCard: (body: AdminCardInput) => send<AdminCard>("/admin/cards", "POST", body),
  toggleCard: (id: number) => send<AdminCard>(`/admin/cards/${id}/toggle`, "PATCH"),

  // ---------- promo ----------
  listPromo: () => request<AdminPromoCode[]>("/admin/promo"),
  createPromo: (body: PromoCodeInput) => send<AdminPromoCode>("/admin/promo", "POST", body),
};

export { ApiError };
