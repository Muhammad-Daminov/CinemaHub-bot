# PROJECT_CONTEXT.md

Architecture and feature inventory. **Status of record** — if this disagrees with the code, fix this file.

_Last full audit: 2026-08-05 · updated through Phase 3 (2026-08-07)_

---

## 1. Product

**CinemaHub Pro** — a Telegram-first streaming service for films, serials, anime, cartoons, and dramas, aimed at an Uzbek-speaking audience (with Russian and English support).

Users browse and watch entirely inside Telegram. Delivered video **stays in the chat** until the user or an admin removes it — the former 15-minute auto-deletion was removed in Phase 3 at the owner's direction. Monetization is manual bank transfer: a user sends a payment screenshot, an admin approves it, and balance or a premium subscription is granted.

**Two front ends, one backend, one database:**

| Surface | Tech | Purpose |
|---|---|---|
| Telegram bot | aiogram 3, webhook | Browse, watch, pay, AI recommendations, profile |
| Mini App | React 18 + Vite + Tailwind, served at `/miniapp` | Visual catalog, search, settings |
| Admin panel | Same React bundle, `view === "admin"` | Content, users, receipts, promos, stats |

---

## 2. Stack

- **Backend:** Python 3.14, FastAPI, aiogram 3.15+, SQLAlchemy 2.0 (async), Alembic
- **Database:** PostgreSQL on **Neon** (`neondb`, pooled endpoint, `us-east-1`)
- **Cache/queue:** Redis — FSM storage, throttling, AI quota, TMDB response cache
- **Frontend:** React 18, TypeScript, Vite 5, Tailwind, lucide-react
- **External APIs:** TMDB (metadata enrichment), Google Gemini (`gemini-2.5-flash`, structured output)
- **Hosting:** Render (single web service serves API + bot webhook + static Mini App)

Roughly 7,500 lines of Python and 4,700 lines of TypeScript, plus a 106-test suite added in Phases 0–3.

---

## 3. Architecture

```
Telegram ──webhook──┐
                    ├──> FastAPI (app/main.py) ──> PostgreSQL (Neon)
Mini App ──REST─────┘         │                 └─> Redis
                              └──> TMDB, Gemini
```

**Single process.** `app/main.py` mounts the aiogram dispatcher on `POST /webhook/telegram`, the REST API under `/api/*`, and the built Mini App at `/miniapp`. Scheduled maintenance (`app/tasks/cron.py`) is a **separate** process meant to run as a Render Cron Job.

### Layers

| Path | Responsibility |
|---|---|
| `app/api/` | REST: `auth`, `i18n`, `movies`, `admin` |
| `app/bot/handlers/` | 9 aiogram routers |
| `app/bot/middlewares/` | Throttling → DB session → i18n (order matters) |
| `app/services/` | All business logic; shared by bot and API |
| `app/db/models/` | SQLAlchemy models, all tables prefixed `chp_` |
| `app/core/` | Config, i18n catalogs, Redis, admin checks, codegen, genres |
| `app/locales/` | The only home for user-facing strings |

**Read/write split:** `services/content.py` owns viewer-facing reads and delivery; `services/admin_content.py` owns admin mutations. A bug in a public catalog route cannot reach the write methods.

---

## 4. Data model — 20 tables

**Users & money**
- `chp_users` — telegram_id, `role` (user/moderator/admin/**super_admin**), `referral_code`, `referred_by_id`, balance, AI counters, `is_banned`, `language`, `language_selected`
- `chp_subscriptions` — `plan_id` (authoritative) + legacy `plan` enum, `expires_at`, `auto_renew`
- `chp_subscription_plans` — price, duration, benefits, ordering, active/free flags. Any number of plans; edited entirely from the admin panel
- `chp_subscription_features` + `chp_plan_features` — a capability defined once, granted per plan with an optional value for quantitative limits
- `chp_balance_history` — signed ledger; `tx_type` ∈ topup / deduction / refund / admin_adjustment / promo_credit. A partial unique index on (user_id, tx_type, reference_id) blocks duplicate credits.
- `chp_admin_permissions` — one row per capability granted to an administrator; the Super Admin holds all of them implicitly and has no rows

**Content**
- `chp_titles` — content_type, name, year, `genres` (Postgres array), country, tmdb_id, poster, rating, view_count, `is_active`, `is_manual_override`
- `chp_episodes` — season, number, duration
- `chp_media_files` — Telegram `file_id`, `language` (uz_dub / uz_sub / ru / en / original), quality
- `chp_collections` + `chp_title_collections` (M2M)
- `chp_watch_history`, `chp_favorites`, `chp_pending_uploads`

**Payments & promo**
- `chp_admin_cards`, `chp_payment_receipts` (pending/approved/rejected; purpose topup/subscription)
- `chp_promo_codes`, `chp_promo_usages`

**Hierarchy:** `Title → Episode → MediaFile`. A film is a Title with one Episode. A title is only "watchable" if it has at least one MediaFile — enforced everywhere by the single `_has_playable_file()` correlated EXISTS.

**Migrations:** 12 revisions, head `f6b2d94ae713`, applied to production 2026-08-07.

---

## 5. Feature inventory

### ✅ Complete

**Bot**
- Onboarding with language selection; deep-link referral capture (`REF_<code>`)
- Catalog browse: genres, collections, search, pagination, seasons/episodes
- Favorites (toggle + list), continue-watching
- Streaming delivery with language fallback (uz_dub → uz_sub → any)
- Payments: top-up and subscription via receipt photo + admin card; admin approve/reject inline
- Promo codes: admin creation (`/createpromo`), user redemption
- Admin upload capture — forwarded video becomes a `PendingUpload`
- AI recommendations (Gemini, constrained to real catalog ids, quota-enforced)
- Profile with watch stats and achievement ranks

**Mini App**
- Home rows: recommended, continue, newest, top, by type, collections
- Rotating hero banner, search, movie detail sheet with similar titles
- Season/episode selector with paged infinite scroll, per-episode audio badges and watched markers. No play control can start an episode the viewer did not choose, and `POST /watch` refuses an ambiguous request (422) rather than defaulting to episode 1 — enforced server-side, not just in the UI
- Audio-language filter applied across **every** catalog row
- First-open language picker; settings page (name, Telegram ID, balance, premium, referral code with copy, language switcher)
- Light/dark theme

**Admin panel** — 46 REST endpoints, each gated on a named permission; dashboard, stats, content + title editor, TMDB search/enrich, collections, promos, receipts (with photo proxy), pending uploads, users, cards, and administrator management (Super Admin only)

**Platform**
- i18n: 3 languages, 202 keys, one catalog shared by bot and Mini App
- Telegram `initData` HMAC auth; authorization by role + 19 granular permissions, enforced through one function shared by the API and the bot (`services/permissions.has_permission`)
- Redis-backed throttling, AI quota (self-expiring daily keys), TMDB cache

### ⚠️ Partial

| Feature | Built | Gap |
|---|---|---|
| **Premium** | Sold, tracked, displayed | Unlocks **only** unlimited AI. No content, quality, or ad benefit. Weak value proposition for the price. |
| **Referral** | Code generated, deep-link capture writes `referred_by_id` | **No reward is ever granted** to either party. The Mini App invites users to share a code that pays nothing. |
| **Balance** | Credited *and spent* — subscriptions are purchasable from it in the Mini App (Phase 5) | `REFUND` remains unused; there is no cancellation or refund path. |
| **Percentage-discount promo** | Stored, redeemable, usage recorded | No checkout to apply it to — explicitly deferred in `services/promo.py`. |
| **Subscription plans** | Manageable as data (Phase 4) and purchasable (Phase 5), with tier upgrade/queue rules | Features are recorded and displayed but **not enforced** — nothing branches on them yet. |
| **Ban** | `is_banned` column, shown in admin users list | **Not enforced anywhere**, and no endpoint sets it. Unchanged by Phase 3, which governs *administrators*, not user bans. |
| **Scheduled maintenance** | `app/tasks/cron.py` correct and idempotent | Never invoked by the app; Render Cron Job config is not in this repo. Unverified whether it runs at all. |
| **Mini App parity** | Browse, search, settings | No favorites, no AI, no premium purchase, no watch stats/ranks — all bot-only. |
| **Order history** | — | Stub message: "coming in a later phase". |

### ❌ Missing

- **Frontend tests** — the 106-test suite is backend-only; no vitest or component tests.
- **`render.yaml`** — infrastructure is still configured in the Render dashboard only.
- **README / onboarding docs** — a new contributor has no entry point.
- **Staging database** — `scripts/test_db.sh` gives a throwaway local cluster for tests, but migrations are still rehearsed there rather than against production-shaped data.
- **Error monitoring** — no Sentry or equivalent; failures surface only in Render logs.
- **REST API rate limiting** — throttling middleware covers the bot only; `/api/*` is unprotected.
- **`auto_renew`** — column exists, zero references outside the model.
- **`monthly_orders_count`** — reset monthly by cron, never incremented.

---

## 6. Environment

Required: `DATABASE_URL`, `REDIS_URL`, `BOT_TOKEN`, `TMDB_API_KEY`, `GEMINI_API_KEY`
Optional: `ADMIN_IDS` (legacy seed only — authority now lives in the database), `WEBHOOK_BASE_URL`, `WEBHOOK_SECRET`, `ENVIRONMENT`, `PORT`, `AI_DAILY_LIMIT_FREE` (3), `SUPER_ADMIN_TELEGRAM_ID`, `PREMIUM_PRICE` (50000), `PREMIUM_SUBSCRIPTION_DAYS` (30), `TOPUP_PRESET_AMOUNTS`

⚠️ `DATABASE_URL` is production. See `CLAUDE.md` §3.

---

## 7. Scale

508 registered users as of 2026-08-05 — 507 on `UZ`, 1 on `EN`. Because `UZ` was also the default for users who never chose, pre-`language_selected` values carry **no** preference signal. Treat legacy `UZ` as "unknown", not "chose Uzbek", in any analysis.
