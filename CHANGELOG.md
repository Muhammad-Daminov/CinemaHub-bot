# CHANGELOG

Notable changes to CinemaHub Pro.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project does not yet tag releases, so entries are grouped by date and commit.

---

## [Unreleased]

### Phase 3 — Identity & Access Control (2026-08-07)

#### Added
- **Super Admin role and a 19-permission system (FR-1).** Administrator authority moved out of the `ADMIN_IDS` environment variable and into the database, where it is a role plus explicit grants.
  - Exactly one Super Admin, named by `SUPER_ADMIN_TELEGRAM_ID`. Promoted on every boot and on contact, with any previous holder demoted — which is how ownership transfers.
  - The Super Admin holds every permission **implicitly** rather than being seeded with rows. Seeded rows can be revoked, and a Super Admin who revoked their own `manage_admins` would lock the platform out of itself.
  - Administrators hold exactly what was granted, one row per capability. Permissions are stored as VARCHAR, so adding one to the vocabulary costs no migration.
  - Grants are diffed rather than rewritten on edit, so an untouched permission keeps its original `granted_at`/`granted_by` — the audit trail is the reason to record them.
- **One enforcement path for both surfaces (FR-1 §4).** `app/services/permissions.has_permission` is the only place the question is answered. The REST API reaches it through `require_permission`, the bot through `app/bot/permissions.py`. `app/core/admin.py` and its `ADMIN_IDS` membership test are deleted, so no second authorization path exists to drift.
- All 41 admin routes now carry a per-route permission alongside the router-wide admin gate, kept so a newly added route is never accidentally public.
- Admin management API and panel: list, appoint, remove, and toggle each permission individually. The permission vocabulary is **served by the backend**, so a capability added in Python appears in the panel without a frontend release.
- `/api/auth/me` returns role and permissions. The Mini App previously discovered admin status by calling an admin-only route and reading the status code — a 403 on every load for every ordinary user.
- Admin panel tabs are filtered by permission, and receipt notifications are addressed to administrators holding `manage_payments` rather than to every configured id — which also stops a user's payment screenshot reaching administrators with no business seeing it.

#### Changed
- **`POST /watch` no longer silently starts episode 1 of a serial.** Omitting `episode_id` is still valid for a title with exactly one episode — a film — and that path is unchanged. For a multi-episode title it now returns **422** with a translated message, because starting episode 1 is wrong for a viewer resuming at episode 40. Enforced at the endpoint rather than only in the UI, so the guarantee does not depend on which client is calling. Covered by `tests/test_watch_endpoint.py`, verified to fail against the old fallback.
- **Auto-deletion of delivered video removed entirely**, at the owner's direction. The Redis delay queue, its background worker, `AUTO_DELETE_SECONDS`, and the "will be deleted in 15 minutes" notice are all gone; the notice is replaced by a plain send confirmation. Films and episodes now stay in the chat until someone deletes them.
- **The Mini App no longer forces playback to start at episode 1.** Phase 2 added the episode picker, but the sheet kept a generic Watch button that silently played the first episode. It is hidden whenever a chooser is on screen, so every play action names its episode.

#### Database
- `c41d5b8ae902` — adds `SUPER_ADMIN` to the `userrole` enum, inside an `autocommit_block`: PostgreSQL refuses to *use* a label added by the still-open transaction that added it, and Alembic runs an entire upgrade as one transaction.
- `d72e4f1c8b35` — creates `chp_admin_permissions` and seeds administrators. Every id previously in `ADMIN_IDS` is promoted with the full permission set, because they already had unrestricted access; narrowing them is the Super Admin's deliberate decision, not a side effect of a deploy.
- **Applied to production 2026-08-07.** Verified afterwards: 1 Super Admin (0 explicit rows, as designed), 1 admin with 19 permissions, no orphaned or non-admin permission rows, 508 users unchanged.


### Phase 2 — Playback & Series Experience (2026-08-07)

#### Added
- **Episode and season navigation in the Mini App (FR-9).** The app previously had no concept of episodes: pressing Watch on a serial always delivered episode 1, however far through it you were. The detail sheet now carries a season strip and a paged episode list, and plays the episode you pick.
  - `GET /api/movies/{id}/seasons` and `GET /api/movies/{id}/episodes` expose season and episode data to viewers. Both already existed in the service layer but were reachable only through `/api/admin`.
  - `POST /api/movies/{id}/watch` accepts an optional `episode_id`. Omitting it still plays the first episode, so existing clients are unaffected.
  - Episodes are paged (30 per page) and loaded by infinite scroll. A long-running serial is never fetched whole to render one screen.
- **Audio tracks shown before playback (FR-7).** Each episode row lists the audio languages that episode actually has, using labels already present in the catalogs. Per episode rather than per title, because a serial can be dubbed only partway through and a title-level list would promise tracks that aren't there. Episodes with no file are marked as such rather than silently appearing playable.
- Watched episodes are marked, using data `chp_watch_history` already held.
- `tests/test_api_schema.py` — builds the OpenAPI schema and asserts the new routes and their contracts. See the regression below for why this exists.

#### Fixed
- **A Phase 0 regression that shipped in `5e98386`: OpenAPI schema generation was broken, so `/docs` and any generated client failed.** Phase 0's review removed `date` from `app/api/admin.py` as an unused import on pyflakes' word. It is used — by `ActivityPointOut.date: date`, where the field name shadows the type, which is why the linter missed it. `import app.main` still succeeded because Pydantic resolves response-model annotations lazily, so nothing caught it until the schema was built. Import restored, marked so it is not deleted again, and covered by a test that fails without it.
- `POST /watch` no longer loads every episode of a serial to take the first one; `first_episode()` asks the database for one row.

#### Security
- The `episode_id` accepted by `/watch` is resolved **through its title** (`get_episode_of_title`). Without that check, any episode id would deliver content the request never asked for. Covered by a test.

#### Notes
- **FR-7 is informational, not a selector.** The request asks to *show* available audio languages before playback; it says nothing about choosing one. Playback still picks a track via the existing `pick_file` fallback chain. Making the display interactive is a small follow-on, recorded in `IDEAS.md`.

### Phase 1 — Correctness Quick Wins (2026-08-05)

#### Fixed
- **The Mini App showed English to everyone after starting a video.** `POST /api/movies/{id}/watch` returned a hardcoded English confirmation, and the Mini App renders that string straight into a toast. The same endpoint's error responses were hardcoded English too, and `App.tsx` renders `detail` verbatim as well. Every string this endpoint returns is now resolved from the caller's language before it leaves the server.
- **An internal diagnostic was being shown to users.** When video delivery failed, the endpoint returned `detail=str(exc)` — a message naming the offending `MediaFile` row. That text went to the user's screen. It is now logged server-side, and the user gets a translated, actionable message instead.
- **`/start` could leave a user permanently unasked about language.** The picker was gated on whether the user row was new. Someone who received the picker and closed the chat without answering still had a row, so their next `/start` counted them as existing and sent them to the main menu — in Uzbek, the default they never chose, and they were never asked again. The gate is now `language_selected`, so the question is repeated until it is actually answered. No existing user is affected: all 508 were backfilled as answered by migration `6b7ec8ebd218`.
- **Telegram profile changes were never picked up.** `username` and `full_name` were written once at signup and never again, so anyone who changed their Telegram handle or display name kept the stale value forever — visible in the admin user list, the welcome message, and Mini App settings. `get_or_create_user` now refreshes them when Telegram reports a change, assigning only on an actual difference so a read does not become a write on every update. A missing `full_name` no longer blanks a known one.
- Nine hardcoded Uzbek strings in the bot's admin flows (receipt approval, rejection, upload handling) now come from the locale catalogs, so they follow the admin's own language.
- Hardcoded English `aria-label`s in the Mini App's theme toggle are now translated.

#### Added
- 11 locale keys across all three catalogs (196 total): `app.watch_sent`, `app.theme_light`, `app.theme_dark`, and the `admin.*` set for the bot's admin flows.
- 19 tests: user provisioning and profile refresh (11), and i18n coverage for the API-facing keys (8) — including a check that each is genuinely distinct per language, since three identical strings would pass a parity check while leaving non-English users reading English.

#### Deferred, with reason
- **Localizing the React admin panel.** The audit behind the roadmap reported it as "inconsistently localized"; it is in fact **entirely** hardcoded Uzbek — roughly 98 user-visible strings across 11 files, using none of the i18n system. Extracting them now means doing it again against the markup FR-3 rewrites in Phase 6, so it moves there and is recorded in `TASKS.md`. The bot's admin strings were localized regardless: there were only nine, and they are bot messages, which is exactly what FR-6 cites.

### Fixed
- **Promo codes with an expiry date could never be redeemed.** `PromoCode.valid_until` is a timezone-aware column, but `_validate` compared it against a naive `datetime.utcnow()`, so redemption raised `TypeError: can't compare offset-naive and offset-aware datetimes`. Codes without an expiry worked, which is why it went unnoticed. Every naive `utcnow()` touching a timezone-aware column is now `datetime.now(timezone.utc)` — in `promo.py`, `payment_review.py`, `admin_promo.py`, and the `Subscription.is_active` property, which had the same latent fault.
- **Promo redemption could exceed its usage cap.** `promo.current_uses += 1` was a read-modify-write, so several users redeeming the last slot at once each saw `current_uses < max_uses` and each proceeded — a code capped at 2 could be redeemed 5 times. Claiming a use is now a single conditional `UPDATE … WHERE max_uses IS NULL OR current_uses < max_uses`, with a zero rowcount meaning the code is exhausted. The use is claimed *before* the effect is applied, so an exhausted code never does work that must be undone.
- **Promo balance credits could be lost to a race** — the same read-modify-write as receipt approval, now an atomic in-database increment.
- **Receipt approval could credit a payment more than once.** The status guard in `approve_receipt` was a plain in-memory check with no row lock, so approvals arriving together all read `PENDING` and all proceeded. In production this turned one receipt into five balance-ledger rows and five subscriptions, one of which stacked an extra 30 days of premium. Approval now selects the receipt `FOR UPDATE`, so the second arrival waits for the first to commit and is then correctly refused.
- **Balance credits could be lost to a race.** `user.balance = user.balance + amount` is a read-modify-write; concurrent writers all read the same starting value and the last write wins — which is why five credits of 50,000 collapsed into a single 50,000. The credit is now an atomic `UPDATE … SET balance = balance + :amount` evaluated in the database.
- **`GEMINI_MODEL` was declared twice** in `app/core/config.py`, so edits to the first declaration silently did nothing. Now declared once, keeping the value that was in force. No runtime change — the deployed `.env` already overrode both.

### Changed
- **A subscription receipt no longer credits the balance.** Approval previously credited the full amount *and* activated the subscription, so paying 50,000 for premium returned 50,000 in spendable balance — the platform kept nothing. Balance is credited for `TOPUP` receipts only; a `SUBSCRIPTION` receipt grants subscription time and nothing else. No balance ledger row is written for it, since no balance moves.
- `approve_receipt` and `reject_receipt` now take a receipt **id** rather than a pre-loaded row, and perform the locked fetch themselves. The lock is what makes the status guard meaningful, so owning the fetch means no call site can forget it. Callers in `app/api/admin.py` and `app/bot/handlers/admin_payment.py` updated; a new `ReceiptNotFoundError` carries the 404 case that callers previously handled by pre-loading.

### Added
- **Test suite** — first automated tests in the project: **45 tests, all passing** against a real PostgreSQL. Covers Telegram `initData` verification (signature forgery, tampered user id, expiry, appended fields), receipt approval and rejection, promo redemption, i18n catalog parity and fallback, and settings parsing. Three are concurrency regression tests, each verified to fail against the pre-fix code: 5 simultaneous approvals of one receipt all succeeded, concurrent promo credits summed to one third of their true total, and a code capped at 2 uses was redeemed 5 times. `tests/conftest.py` refuses any `TEST_DATABASE_URL` on a Neon host, since the suite drops and recreates every table.
- **`scripts/test_db.sh`** — starts a throwaway PostgreSQL cluster owned by the current user on a non-standard port. No sudo, no interference with a system PostgreSQL, nothing shared with production. SQLite cannot substitute here: the models use Postgres arrays and the concurrency tests need real row locks. Without a database the suite still runs, skipping the 20 database-backed tests.
- **`scripts/check_locales.py`** — locale parity gate. Verifies `uz`/`ru`/`en` carry identical key sets and that every key referenced in code resolves in the fallback catalog. Recognises all four translator call shapes in use (`t(…)`, `_(…)`, `PromoError(…)`, `t_for_user(…)`). Exits non-zero on failure.
- **CI workflow** (`.github/workflows/ci.yml`) — import check, locale parity, tests, and frontend build on every push and pull request.
- **`requirements-dev.txt`** — development dependencies, separate from runtime.
- Migration `a3f1c92d7e04` — a partial unique index on `chp_balance_history (user_id, tx_type, reference_id)` as a database-level backstop against duplicate credits. Scoped per user and transaction type rather than on `reference_id` alone, because two users legitimately redeem the same promo code; partial on `reference_id IS NOT NULL` so admin adjustments, which carry no reference, stay unconstrained. **Applied to production 2026-08-05.**
- `CLAUDE.md`, `PROJECT_CONTEXT.md`, `TASKS.md`, `IDEAS.md`, `CHANGELOG.md` — project documentation set, established as the single source of truth (2026-08-05).
- `FEATURE_REQUESTS.md`, `IMPLEMENTATION_STATUS.md`, `IMPLEMENTATION_ROADMAP.md` — product specification, codebase comparison, and phased delivery plan (2026-08-05).

### Removed
- `webapp/tsconfig.tsbuildinfo` untracked and gitignored — an incremental build artefact that churned on every build.

### Database — production, 2026-08-05
- **Receipt #1 duplication cleaned up.** The payment was a test transaction, so the ledger was reconciled to the corrected rules: four duplicate subscriptions deleted (including the one granting an extra 30 days of premium), all five duplicate ledger rows deleted, and the affected balance reset to zero. Executed in a single transaction that asserted its own end state before committing. The single correct 30-day term (2026-08-01 → 2026-08-31) was retained.
- **Consistency verified afterwards** across all 508 users: `balance` equals the sum of each user's ledger for every account, no duplicate ledger events, no negative balances, no orphaned ledger or subscription rows, and every promo counter matches its recorded usages.

### Known, not addressed
- `Subscription.is_active` in `app/db/models/user.py` is dead code — nothing reads it. Its naive/aware comparison bug was fixed rather than the property removed, since deleting model API is a separate decision.
- `app/api/movies.py` imports `Collection` without using it. Pre-existing and outside Phase 0's scope; that file is touched in Phase 1.

---

## 2026-08-05 — `9bd6d48` + follow-ups

### Added
- **Mini App internationalization.** `GET /api/i18n/{lang}` serves the bot's own `app/locales/*.json` catalogs to the frontend, merging the requested language over Uzbek server-side. `webapp/src/lib/i18n.ts` provides `translate()`, `fetchTranslations()`, and a React context; `I18nProvider` binds it to the tree. The Mini App no longer carries any strings of its own — one wording change now reaches both surfaces.
- **First-open language picker.** New `chp_users.language_selected` column distinguishes a real choice from the `UZ` default. `PATCH /api/auth/me` accepts `{language}` and sets the flag. Because the column is shared with the bot, switching language in the Mini App also switches the bot's replies.
- **Mini App settings page** — name, Telegram ID, balance, premium status, referral code with copy button, and language switcher.
- **Audio-language filter across the catalog.** `AudioFilter` chip strip in the home and search views; `audio_language` accepted by all six catalog endpoints — `/movies`, `/movies/top`, `/movies/search`, `/movies/recommended`, `/movies/continue`, `/movies/{id}/similar`. Every row that shows catalog titles now obeys the filter, including similar titles in the detail sheet.
- `app.search_clear` locale key — the search-clear button's `aria-label` was the last hardcoded user-facing string.

### Changed
- `continue_watching()` gained an optional `audio_language`, applying the existing `_has_playable_file()` correlated EXISTS. Filtering is deliberately **title-level**: a per-episode test would drop a half-watched serial whenever the exact episode someone stopped on lacked that track.
- `api.recommended()`, `api.continueWatching()`, and `api.similar()` route their parameters through `toQuery()` instead of hand-built query strings.
- CORS `allow_methods` now includes `PATCH`, without which the production preflight for the language switch fails silently.

### Database
- Migration `6b7ec8ebd218` — adds `chp_users.language_selected` (`NOT NULL`, `server_default false`), backfilling all existing rows to `true` so current users are not interrupted by a picker for a setting they have been living with. **Applied to production 2026-08-05**; verified 508/508 users backfilled.

---

## 2026-08-03

### Added — `7988371`
- Similar-titles recommendations, scored by shared collections, genre overlap, content type, and release proximity, evaluated entirely in Postgres as correlated subqueries — no candidate crosses the wire to be discarded.
- Personalised home rows derived from watch history, falling back to popularity for users with none.
- Rotating hero banner on the Mini App home screen.

### Added — `3588395`
- Favorites: toggle and list, in the bot and the catalog service.
- AI quota refund — a failed Gemini call no longer consumes the user's daily allowance.

### Changed — `920c4ec`
- Normalised genre vocabulary into `app/core/genres.py`, replacing the frontend's `webapp/src/lib/genres.ts`. Genre-overlap scoring can only match on identical strings, so a single canonical vocabulary is a correctness requirement, not tidiness.

### Fixed
- Webhook is no longer deleted on shutdown, and pending updates survive a redeploy (`drop_pending_updates=False`) — a redeploy takes long enough that dropping the queue discards every button pressed during it. (`1f3192f`)
- Dark-mode `<select>` styling. (`fb2e20b`)
- Movie detail sheet bottom padding — the admin bottom nav was painting over the watch button. (`3588395`)

---

## 2026-08-03 — `6578586`

### Fixed
- Health check now answers `HEAD` as well as `GET`.
- Duplicate episode handling on upload.

---

## 2026-08-02 — `3a3cc64`

Initial CinemaHub Pro architecture.

### Added
- **Data model** — `Title → Episode → MediaFile` hierarchy replacing the legacy flat movie table, with collections (M2M), watch history, favorites, and pending uploads. All tables prefixed `chp_`.
- **Telegram bot** (aiogram 3, webhook) — onboarding with language selection, referral capture via `start` deep links, catalog browsing by genre and collection, search, pagination, seasons and episodes, streaming delivery with audio-language fallback, profile with watch stats and achievement ranks.
- **Payments** — manual bank transfer: admin cards, receipt photo upload, admin approve/reject inline in the bot and via REST. Balance credit and subscription activation share one code path so the two can never drift.
- **Promo codes** — balance, premium-days, and percentage-discount types; admin creation via `/createpromo`, user redemption with max-use and expiry enforcement.
- **AI recommendations** — Gemini structured output constrained to a slice of the real catalog, with results filtered against the same id set again so a hallucinated id can never surface.
- **Auto-delete** — 15-minute copyright-safety window backed by a Redis sorted-set delay queue, which survives the redeploys that would lose in-memory timers.
- **Mini App** (React + Vite + Tailwind) — catalog browse, search, detail sheet, light/dark theme.
- **Admin panel** — 41 REST endpoints behind `initData` + `ADMIN_IDS`: dashboard, stats, content and title editing, TMDB search and enrichment, collections, promos, receipts with photo proxy, pending uploads, users, cards.
- **i18n** — Uzbek, Russian, and English catalogs with Uzbek as fallback.
- **Platform** — Telegram `initData` HMAC verification, Redis-backed throttling and FSM storage, TMDB response caching, idempotent scheduled maintenance script.

### Migrations
`7e01018a8f60` (baseline) · `2b6a819e2a3d` (Title/Episode/MediaFile) · `cfa148caa42b` (collections) · `78dab4021c4a` (watch history) · `9eed61bb0705` (pending uploads) · `68eba9169142` (favorites)
