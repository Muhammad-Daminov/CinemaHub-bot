import { getInitData } from "./telegram";
import type {
  AudioLanguageFilter,
  BannerSlide,
  BillingOverview,
  EpisodePage,
  HeldSubscription,
  Movie,
  MovieCollection,
  MovieContentType,
  PaymentCard,
  PaymentHistoryEntry,
  PurchasePreview,
  ReceiptStatus,
  ResolvedTheme,
  UserProfile,
  WatchResponse,
} from "../types/movie";
import type {
  ActivityPoint,
  AdminAccount,
  AdminCard,
  AdminCardInput,
  AdminCollection,
  AdminCollectionListItem,
  AdminEpisode,
  AdminMediaFile,
  AdminPromoCode,
  AdminReceipt,
  AdminBanner,
  AdminRejectionReason,
  AdminTheme,
  AdminThemeAssignment,
  BannerInput,
  ThemeAssignmentInput,
  ThemeInput,
  ThemeVocabulary,
  AdminBroadcast,
  AdminBroadcastDetail,
  BroadcastEstimate,
  BroadcastInput,
  BroadcastTargets,
  AdminStats,
  AdminTitle,
  AdminTitleTranslation,
  AdminUser,
  BroadcastAudience,
  BroadcastAudienceSize,
  MembershipSettings,
  TrialSettings,
  EpisodeInput,
  MediaFileInput,
  PaymentStatus,
  PendingAttachInput,
  PendingUpload,
  PermissionCatalog,
  PlanInput,
  PlanUpdateInput,
  PromoCodeInput,
  CollectionInput,
  CollectionUpdateInput,
  SimilarTitle,
  StatusResponse,
  SubscriptionFeature,
  SubscriptionPlan,
  TMDBSearchResult,
  TitleInput,
  TitleListParams,
  TitleTranslationInput,
  TitlePage,
  TitleUpdateInput,
  TopUser,
  UserListParams,
  UserPage,
} from "../types/admin";

const API_BASE = "/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }

  /**
   * The API is rate-limited per caller. Callers check this rather than the
   * raw status so the message shown is the viewer's own language — the
   * backend's 429 body is English, because a middleware running before
   * authentication has no user whose language it could read.
   */
  get isRateLimited(): boolean {
    return this.status === 429;
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
  /** The caller's resolved theme. Takes no id — the server derives it. */
  theme: () => request<ResolvedTheme>("/auth/me/theme"),
  setLanguage: (language: string) =>
    request<UserProfile>("/auth/me", { method: "PATCH", body: JSON.stringify({ language }) }),
  listMovies: (
    params: {
      skip?: number;
      limit?: number;
      genre?: string;
      collection_id?: number;
      content_type?: MovieContentType;
      audio_language?: AudioLanguageFilter;
    } = {},
  ) => request<Movie[]>(`/movies${toQuery(params)}`),
  /**
   * One title, freshly resolved for the caller.
   *
   * Exists so a card held in state can be re-asked after something that
   * changes entitlement — buying a subscription, most of all. `is_locked`
   * is decided by the server per request, so a snapshot taken before the
   * purchase would keep showing a padlock until the app was navigated away
   * from and back.
   */
  movie: (movieId: number) => request<Movie>(`/movies/${movieId}`),
  topMovies: (limit = 10, audio_language?: AudioLanguageFilter) =>
    request<Movie[]>(`/movies/top${toQuery({ limit, audio_language })}`),
  searchMovies: (q: string, audio_language?: AudioLanguageFilter) =>
    request<Movie[]>(`/movies/search${toQuery({ q, audio_language })}`),
  recommended: (limit = 10, audio_language?: AudioLanguageFilter) =>
    request<Movie[]>(`/movies/recommended${toQuery({ limit, audio_language })}`),
  continueWatching: (limit = 10, audio_language?: AudioLanguageFilter) =>
    request<Movie[]>(`/movies/continue${toQuery({ limit, audio_language })}`),
  similar: (movieId: number, limit = 10, audio_language?: AudioLanguageFilter) =>
    request<Movie[]>(`/movies/${movieId}/similar${toQuery({ limit, audio_language })}`),
  collections: () => request<MovieCollection[]>("/movies/collections"),
  /** Hero slides for the calling user. Empty means "no campaigns" and the
   *  app falls back to deriving a banner from the newest/top rows. */
  banners: () => request<BannerSlide[]>("/movies/banners"),
  seasons: (movieId: number) => request<number[]>(`/movies/${movieId}/seasons`),
  episodes: (movieId: number, season?: number, page = 0) =>
    request<EpisodePage>(`/movies/${movieId}/episodes${toQuery({ season, page })}`),
  // episode_id omitted plays the first episode, which is what a film has.

  // ---------- favourites (Phase 6) ----------
  favorites: (page = 0) => request<Movie[]>(`/movies/favorites${toQuery({ page })}`),
  /** Returns the resulting state, so the heart renders what the server decided. */
  toggleFavorite: (movieId: number) =>
    request<{ title_id: number; is_favorite: boolean }>(`/movies/${movieId}/favorite`, {
      method: "POST",
    }),
  removeFavorite: (movieId: number) =>
    request<{ title_id: number; is_favorite: boolean }>(`/movies/${movieId}/favorite`, {
      method: "DELETE",
    }),

  // ---------- billing (Phase 5) ----------
  billingOverview: () => request<BillingOverview>("/billing/overview"),
  previewPurchase: (planId: number) =>
    request<PurchasePreview>(`/billing/plans/${planId}/preview`),
  purchasePlan: (planId: number) =>
    request<HeldSubscription>(`/billing/plans/${planId}/purchase`, { method: "POST" }),
  paymentCards: () => request<PaymentCard[]>("/billing/cards"),
  paymentHistory: () => request<PaymentHistoryEntry[]>("/billing/history"),
  /** One of the caller's own payments. The backend 404s anyone else's. */
  receiptStatus: (receiptId: number) =>
    request<ReceiptStatus>(`/billing/receipts/${receiptId}`),
  /** Multipart: the receipt is already bytes, and base64 would inflate it by a third. */
  submitTopup: async (amount: number, cardId: number, file: File) => {
    const body = new FormData();
    body.append("amount", String(amount));
    body.append("card_id", String(cardId));
    body.append("receipt", file);
    const response = await fetch(`${API_BASE}/billing/topup`, {
      method: "POST",
      // No Content-Type: the browser must set the multipart boundary itself.
      headers: { "X-Telegram-Init-Data": getInitData() },
      body,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ detail: response.statusText }));
      throw new ApiError(response.status, payload.detail ?? "Request failed");
    }
    return response.json() as Promise<{ status: string; message: string }>;
  },

  watchMovie: (movieId: number, episodeId?: number) =>
    request<WatchResponse>(`/movies/${movieId}/watch${toQuery({ episode_id: episodeId })}`, {
      method: "POST",
    }),
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

/** Shared multipart POST for image uploads. */
async function uploadImage(path: string, field: string, file: File) {
  const body = new FormData();
  body.append(field, file);
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "X-Telegram-Init-Data": getInitData() },
    body,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, payload.detail ?? "Upload failed");
  }
  return response.json() as Promise<{ status: string; image_id: number }>;
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
  /** One title by id. The editor refreshes through this — scanning a page of
   *  the list silently missed any title outside the newest 100. */
  getTitle: (id: number) => request<AdminTitle>(`/admin/titles/${id}`),
  createTitle: (body: TitleInput) => send<AdminTitle>("/admin/titles", "POST", body),
  updateTitle: (id: number, body: TitleUpdateInput) =>
    send<AdminTitle>(`/admin/titles/${id}`, "PATCH", body),
  toggleTitle: (id: number) => send<AdminTitle>(`/admin/titles/${id}/toggle`, "PATCH"),
  deleteTitle: (id: number) => send<StatusResponse>(`/admin/titles/${id}`, "DELETE"),
  enrichTitle: (id: number) => send<AdminTitle>(`/admin/titles/${id}/enrich`, "POST"),

  // ---------- catalog translations (Phase 7) ----------
  titleTranslations: (id: number) =>
    request<AdminTitleTranslation[]>(`/admin/titles/${id}/translations`),
  setTitleTranslations: (id: number, translations: TitleTranslationInput[]) =>
    send<AdminTitleTranslation[]>(`/admin/titles/${id}/translations`, "PUT", { translations }),
  /** Pulls ru/en from TMDB. Manual translations are preserved by the backend. */
  fillTitleTranslations: (id: number) =>
    send<AdminTitleTranslation[]>(`/admin/titles/${id}/translations/tmdb`, "POST"),

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

  // ---------- collections ----------
  listCollections: () => request<AdminCollectionListItem[]>("/admin/collections"),
  createCollection: (body: CollectionInput) =>
    send<AdminCollection>("/admin/collections", "POST", body),
  updateCollection: (id: number, body: CollectionUpdateInput) =>
    send<AdminCollection>(`/admin/collections/${id}`, "PATCH", body),
  toggleCollection: (id: number) =>
    send<AdminCollection>(`/admin/collections/${id}/toggle`, "PATCH"),
  deleteCollection: (id: number) =>
    send<StatusResponse>(`/admin/collections/${id}`, "DELETE"),
  collectionTitles: (id: number) => request<AdminTitle[]>(`/admin/collections/${id}/titles`),
  addTitleToCollection: (id: number, titleId: number) =>
    send<StatusResponse>(`/admin/collections/${id}/titles`, "POST", { title_id: titleId }),
  removeTitleFromCollection: (id: number, titleId: number) =>
    send<StatusResponse>(`/admin/collections/${id}/titles/${titleId}`, "DELETE"),
  titleCollections: (titleId: number) => request<number[]>(`/admin/titles/${titleId}/collections`),
  setTitleCollections: (titleId: number, collectionIds: number[]) =>
    send<number[]>(`/admin/titles/${titleId}/collections`, "PUT", { collection_ids: collectionIds }),

  // ---------- TMDB manual search ----------
  searchTmdb: (q: string) =>
    request<TMDBSearchResult[]>(`/admin/tmdb/search?q=${encodeURIComponent(q)}`),
  applyTmdbMatch: (titleId: number, tmdbId: number) =>
    send<AdminTitle>(`/admin/titles/${titleId}/tmdb/${tmdbId}`, "POST"),

  // ---------- duplicate detection ----------
  similarTitles: (name: string) =>
    request<SimilarTitle[]>(`/admin/titles/similar?name=${encodeURIComponent(name)}`),

  // ---------- users ----------
  listUsers: (params: UserListParams = {}) => request<UserPage>(`/admin/users${toQuery(params)}`),
  setUserBan: (userId: number, banned: boolean) =>
    send<AdminUser>(`/admin/users/${userId}/ban`, "PATCH", { banned }),

  // ---------- receipts ----------
  /** `status` omitted means every state — that is the history view, not the queue. */
  listReceipts: (params: { status?: PaymentStatus; q?: string } = {}) =>
    request<AdminReceipt[]>(`/admin/receipts${toQuery(params)}`),
  approveReceipt: (id: number) => send<StatusResponse>(`/admin/receipts/${id}/approve`, "POST"),
  rejectReceipt: (id: number, notes: string | null, reason_id: number | null) =>
    send<StatusResponse>(`/admin/receipts/${id}/reject`, "POST", { notes, reason_id }),
  /** Neither figure is credited — the user is told both and can resubmit. */
  flagMismatch: (id: number, verified_amount: number, notes?: string) =>
    send<StatusResponse>(`/admin/receipts/${id}/mismatch`, "POST", { verified_amount, notes }),
  rejectionReasons: () => request<AdminRejectionReason[]>("/admin/rejection-reasons"),

  // ---------- cards ----------
  listCards: () => request<AdminCard[]>("/admin/cards"),
  createCard: (body: AdminCardInput) => send<AdminCard>("/admin/cards", "POST", body),
  toggleCard: (id: number) => send<AdminCard>(`/admin/cards/${id}/toggle`, "PATCH"),

  // ---------- posters ----------
  /**
   * Multipart, like the receipt upload — the file is already bytes and
   * base64 would inflate it by a third. No Content-Type header: the
   * browser must set the multipart boundary itself.
   */
  uploadTitlePoster: async (titleId: number, file: File) =>
    uploadImage(`/admin/titles/${titleId}/poster`, "poster", file),
  clearTitlePoster: (titleId: number) =>
    send<StatusResponse>(`/admin/titles/${titleId}/poster`, "DELETE"),
  uploadCollectionPoster: async (collectionId: number, file: File) =>
    uploadImage(`/admin/collections/${collectionId}/poster`, "poster", file),
  clearCollectionPoster: (collectionId: number) =>
    send<StatusResponse>(`/admin/collections/${collectionId}/poster`, "DELETE"),

  // ---------- subscription plans & features ----------
  listPlans: () => request<SubscriptionPlan[]>("/admin/plans"),
  createPlan: (body: PlanInput) => send<SubscriptionPlan>("/admin/plans", "POST", body),
  updatePlan: (id: number, body: PlanUpdateInput) =>
    send<SubscriptionPlan>(`/admin/plans/${id}`, "PATCH", body),
  togglePlan: (id: number) => send<SubscriptionPlan>(`/admin/plans/${id}/toggle`, "PATCH"),
  deletePlan: (id: number) => send<StatusResponse>(`/admin/plans/${id}`, "DELETE"),
  reorderPlans: (planIds: number[]) =>
    send<SubscriptionPlan[]>("/admin/plans/reorder", "POST", { plan_ids: planIds }),
  setPlanFeatures: (id: number, grants: Record<number, string | null>) =>
    send<SubscriptionPlan>(`/admin/plans/${id}/features`, "PUT", { grants }),

  listSubscriptionFeatures: () => request<SubscriptionFeature[]>("/admin/features"),
  createSubscriptionFeature: (body: { code: string; name: string; description?: string | null }) =>
    send<SubscriptionFeature>("/admin/features", "POST", body),
  deleteSubscriptionFeature: (id: number) =>
    send<StatusResponse>(`/admin/features/${id}`, "DELETE"),

  // ---------- administrators & permissions ----------
  permissionCatalog: () => request<PermissionCatalog>("/admin/permissions"),
  listAdmins: () => request<AdminAccount[]>("/admin/admins"),
  createAdmin: (telegram_id: number, permissions: string[] = []) =>
    send<AdminAccount>("/admin/admins", "POST", { telegram_id, permissions }),
  setAdminPermissions: (userId: number, permissions: string[]) =>
    send<AdminAccount>(`/admin/admins/${userId}/permissions`, "PUT", { permissions }),
  removeAdmin: (userId: number) => send<StatusResponse>(`/admin/admins/${userId}`, "DELETE"),

  // ---------- broadcasts ----------
  listBroadcasts: () => request<AdminBroadcast[]>("/admin/broadcasts"),
  broadcastAudiences: () => request<BroadcastAudienceSize[]>("/admin/broadcasts/audience"),
  /** The allowlists the backend validates against — never hardcoded client-side. */
  broadcastTargets: () => request<BroadcastTargets>("/admin/broadcasts/targets"),
  /**
   * The recipient count, computed by the backend through the same
   * eligibility query materialisation uses. Never approximated here.
   */
  broadcastEstimate: (audience: BroadcastAudience, targetValue: string | null) => {
    const params = new URLSearchParams({ audience });
    if (targetValue) params.set("target_value", targetValue);
    return request<BroadcastEstimate>(`/admin/broadcasts/estimate?${params}`);
  },
  /** Live delivery state. The progress screen's only source of truth. */
  broadcastDetail: (id: number) => request<AdminBroadcastDetail>(`/admin/broadcasts/${id}`),
  /** Restarts a recoverable broadcast against its existing recipient rows. */
  resumeBroadcast: (id: number) =>
    send<AdminBroadcast>(`/admin/broadcasts/${id}/resume`, "POST"),
  sendBroadcast: (input: BroadcastInput) =>
    send<AdminBroadcast>("/admin/broadcasts", "POST", input),

  // ---------- system settings ----------
  membershipSettings: () => request<MembershipSettings>("/admin/settings/membership"),
  saveMembershipSettings: (require_membership: boolean, required_channel: string | null) =>
    send<MembershipSettings>("/admin/settings/membership", "PUT", {
      require_membership,
      required_channel,
    }),
  trialSettings: () => request<TrialSettings>("/admin/settings/trial"),
  saveTrialSettings: (enabled: boolean, days: number) =>
    send<TrialSettings>("/admin/settings/trial", "PUT", { enabled, days }),

  // ---------- appearance: themes ----------
  themes: () => request<AdminTheme[]>("/admin/themes"),
  /** Token vocabulary, shape presets and decoration names, from the backend
   *  so the builder can only offer what the validator accepts. */
  themeVocabulary: () => request<ThemeVocabulary>("/admin/themes/tokens"),
  createTheme: (body: ThemeInput) => send<AdminTheme>("/admin/themes", "POST", body),
  /**
   * Saves the whole editable theme in one call: colours, card shape and
   * decoration. `decoration` is an allowlisted key naming a compiled
   * component — never markup, a URL or a CSS value — and the server
   * revalidates it against the same allowlist.
   */
  setThemeTokens: (
    id: number,
    tokens: Record<string, string>,
    card_shape?: string,
    decoration?: string,
  ) =>
    send<AdminTheme>(`/admin/themes/${id}/tokens`, "PUT", { tokens, card_shape, decoration }),
  duplicateTheme: (id: number, key: string, name: string) =>
    send<AdminTheme>(`/admin/themes/${id}/duplicate`, "POST", { key, name, tokens: {} }),
  setDefaultTheme: (id: number) => send<AdminTheme>(`/admin/themes/${id}/default`, "POST"),
  toggleTheme: (id: number) => send<AdminTheme>(`/admin/themes/${id}/toggle`, "PATCH"),
  deleteTheme: (id: number) => send<StatusResponse>(`/admin/themes/${id}`, "DELETE"),

  themeAssignments: () => request<AdminThemeAssignment[]>("/admin/theme-assignments"),
  createThemeAssignment: (body: ThemeAssignmentInput) =>
    send<AdminThemeAssignment>("/admin/theme-assignments", "POST", body),
  deleteThemeAssignment: (id: number) =>
    send<StatusResponse>(`/admin/theme-assignments/${id}`, "DELETE"),

  // ---------- appearance: banners ----------
  banners: () => request<AdminBanner[]>("/admin/banners"),
  bannerLabels: () => request<{ labels: string[] }>("/admin/banners/labels"),
  createBanner: (body: BannerInput) => send<AdminBanner>("/admin/banners", "POST", body),
  updateBanner: (id: number, body: BannerInput) =>
    send<AdminBanner>(`/admin/banners/${id}`, "PATCH", body),
  deleteBanner: (id: number) => send<StatusResponse>(`/admin/banners/${id}`, "DELETE"),

  // ---------- promo ----------
  listPromo: () => request<AdminPromoCode[]>("/admin/promo"),
  createPromo: (body: PromoCodeInput) => send<AdminPromoCode>("/admin/promo", "POST", body),
};

