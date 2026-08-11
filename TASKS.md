# TASKS.md

Prioritized backlog. **Status of record** for what to work on next.

**Status:** `TODO` · `IN PROGRESS` · `BLOCKED` · `DONE` · `WON'T DO`
**Priority:** P0 correctness/risk · P1 foundation · P2 product value · P3 polish

Finished work moves to §Done (kept, not deleted) and gets a `CHANGELOG.md` entry.

---

## P0 — Correctness & risk

### P0-1 · Duplicate `GEMINI_MODEL` in config
`DONE` (Phase 0) · kept for the record
Declared twice; the second (`gemini-2.5-flash`) silently wins. Anyone editing the first sees no effect. Delete the stale line and confirm the intended model.
**Effort:** minutes.

### P0-2 · Subscription receipts credit balance *and* grant premium
`DONE` (Phase 0) · resolved as a row-lock + atomic-increment fix, plus the TOPUP-only credit decision; production ledger cleaned and backstop index applied
`user.balance += receipt.amount` runs unconditionally, then a `SUBSCRIPTION`-purpose receipt *also* activates premium. A user paying 50,000 for premium receives premium **plus** 50,000 in balance.
Not currently exploitable — balance cannot be spent (see P2-1) — but it becomes a live double-credit the moment a spending path exists, and the ledger is already wrong today.
**Decide:** should a subscription receipt credit balance at all? Likely fix is to credit only for `PaymentPurpose.TOPUP`.
**Blocks:** P2-1.

### P0-3 · No staging or local database
`DONE` (Phase 0) for testing via `scripts/test_db.sh`; a Neon branch for migration rehearsal against production-shaped data is still worth having
Every migration and every manual query hits production. Cheapest fix: a **Neon branch** as a dev database, with `DATABASE_URL` switched by environment.
**Effort:** ~1h. **High leverage** — de-risks everything below.

### P0-4 · `is_banned` is not enforced
`DONE` (Phase 6) · enforced in both surfaces and settable from the panel
Enforced by `get_active_user` on every REST route that does something (the catalog, billing, the admin API) and by `AccessMiddleware` on every bot update; `PATCH /api/admin/users/{id}/ban` is the setter, with a confirm step in `UsersPanel.tsx`.
`/api/auth/me` deliberately still answers for a banned user — it is what tells the Mini App to render the blocked notice instead of an empty catalog. The Super Admin is exempt, and a banned administrator keeps their role so the action is reversible.

### P0-5 · Verify scheduled maintenance actually runs
`PARTIALLY DONE` (Phase 8) · **detection built and verified; scheduling deliberately deferred to the VPS migration**
Every completed run now stamps `last_maintenance_run` in `chp_system_settings`, and the web service logs `SCHEDULED MAINTENANCE OVERDUE` on startup when that stamp is missing or older than 48 hours. The application can therefore *tell* you the job is not running — which it could not before.

**Deferred to the VPS migration (owner's decision, 2026-08-09).** The platform is moving off Render to a self-managed server within days, so the scheduler will be configured **there**, not on Render. No Render Cron Job is to be created, and **no `render.yaml` is to be added** — committing a blueprint for infrastructure about to be retired would be work thrown away, and Render treats a blueprint as authoritative, so a wrong one would stand up a second service rather than adopt the existing one.

Nothing in the application is waiting on that migration: the maintenance code is scheduler-agnostic and was verified against a plain `cron`-style invocation (see below). The only outstanding step is a scheduler entry on the new host.

**What the repository *can* state, verified rather than assumed:**

| | |
|---|---|
| Command | `python -m app.tasks.cron` |
| Suggested schedule | daily; the staleness window is 48h, so anything up to once a day is safely inside it |
| Required environment | `DATABASE_URL`, **`BOT_TOKEN`, `TMDB_API_KEY`, `GEMINI_API_KEY`** |
| Optional | `REDIS_URL` (unused by this job, but harmless) |

The three API keys surprise people: the job never calls Telegram, TMDB or Gemini, but `app.core.config.Settings` declares them required, so the process exits on a `ValidationError` without them. Verified by running the module outside the repo so the local `.env` could not mask it. **A scheduler entry carrying only `DATABASE_URL` will crash on every run.**

**Verified ready for a plain scheduler** (2026-08-09, against the throwaway test database, invoked exactly as `cron` would — outside the repo, no `.env` on disk, a minimal environment):

- runs standalone as `python -m app.tasks.cron` and exits **0** on success;
- exits **non-zero** when the database is unreachable, so a failed run is visible to `cron`'s MAILTO or `systemd`'s `OnFailure`;
- **idempotent** — an immediate second run repeats cleanly, so an overlapping or double-fired schedule is not a correctness risk;
- logs one summary line to stdout for the scheduler to capture;
- disposes the connection pool explicitly, so the process exits rather than lingering.

**To configure after the VPS migration** — a crontab entry or a `systemd` timer, whichever the new host standardises on:

```
0 3 * * *  cd /srv/cinemahub && /srv/cinemahub/venv/bin/python -m app.tasks.cron >> /var/log/cinemahub-cron.log 2>&1
```

The environment must carry the four variables above; a `systemd` unit with `EnvironmentFile=` is the tidier route, since `cron` runs with an almost-empty environment and will otherwise fail the config validation described above.

**Interim check, available now:** read the startup log. `SCHEDULED MAINTENANCE OVERDUE` means no scheduler is running the job; its absence is proof it ran within 48 hours. Expect the warning until the VPS scheduler exists — that is the detection half doing its job, not a fault.

---

## P1 — Foundation

### P1-1 · Introduce a test suite
`TODO` · **largest single risk in the project**
Zero tests across ~10,800 lines handling real money and 508 real users. Suggested first targets, highest value first:
1. `verify_init_data` — auth bypass is the worst failure available
2. `payment_review` — credit/activate correctness, double-approval safety
3. `promo.redeem` — max-uses, expiry, reuse prevention
4. `content.pick_file` language fallback chain
5. `_has_playable_file` filtering, incl. audio-language narrowing

Needs pytest + pytest-asyncio and a test database (depends on **P0-3**).

### P1-2 · CI pipeline
`TODO` · `.github/workflows/`
Run `python -c "import app.main"`, `npm run build`, the locale parity check, and (once P1-1 lands) tests on every push.

### P1-3 · Automate the locale parity check
`TODO` · `scripts/check_locales.py`
Currently manual and ad-hoc. Script it: identical key sets across `uz`/`ru`/`en`, and every `t("…")` / `_("…")` key used in `app/` and `webapp/src/` resolves. Wire into CI.

### P1-4 · README
`TODO`
No entry point for a new contributor. Setup, env vars, how to run bot + Mini App, migration workflow, the production-DB warning.

### P1-5 · Rate-limit the REST API
`TODO` · `app/api/`
Bot traffic is throttled via Redis; `/api/*` is not. `/api/movies/search` hits the DB with a user-supplied `ILIKE` on every keystroke (300 ms debounce client-side only).

### P1-6 · Error monitoring
`TODO`
No Sentry or equivalent. Production failures are invisible outside Render logs.

### P1-7 · Deployment config into version control
`TODO`
Put deployment configuration in version control so infrastructure is reviewable and reproducible. **Retarget to the VPS** (owner's decision, 2026-08-09): Render is being retired, so this becomes the service unit / reverse-proxy / scheduler config for the new host rather than a `render.yaml`.

### P1-8 · Stop committing `tsconfig.tsbuildinfo`
`TODO` · `.gitignore`
Build artifact; churns on every build and pollutes diffs.

---

## P2 — Product value

### P2-1 · Make balance spendable
`DONE` (Phase 5) — subscriptions are purchasable from balance in the Mini App
Users can top up but cannot spend. `DEDUCTION`/`REFUND` tx types are unused. Decide what balance buys — most naturally, premium itself (removing the second receipt round trip). Until then, top-up is a dead end that takes money and returns a number.

### P2-2 · Give premium real benefits
`IN PROGRESS` (Phase 8) · the *mechanism* exists; **which** benefits to sell is still a product decision
Features are now enforced, not merely displayed: `app/services/plan_features.py` is the single entitlement decision point, and `ai_daily_limit` is the first feature that actually changes behaviour. Adding a second benefit is now a data change in the admin panel plus one call to the resolver — no parallel entitlement system to build.
**Phase 10 shipped the first content benefit:** `chp_titles.is_premium` marks a title subscribers-only, enforced through `check_title_access` rather than by hiding a button, and a subscription now also exempts the holder from the channel-membership requirement. Premium is no longer "unlimited AI" alone.
**Still needed from you:** the rest of what premium should include, and *which* titles are marked premium — the flag is a per-title editorial decision nobody has made yet, so today every title is free. Candidates unchanged: higher-quality files, early access, larger favourites cap.

### P2-3 · Pay out referral rewards
`DONE` (Phase 6) · rule from IDEAS.md I-2; amount is configuration
[app/services/referral.py](app/services/referral.py) credits **both parties when the referred user's first top-up is approved**, fired from `approve_receipt` inside the same transaction as the credit.
**Decision recorded:** the amount was undefined anywhere in the project, so it is `REFERRAL_BONUS_AMOUNT` (default **5000**, `0` disables) rather than a number invented in code. Revisit once the business figure is settled — changing it is a config edit, not a release.
Paying once is enforced by the partial unique index `uq_balance_history_event`, not by a read-then-write check; the reference is scoped to the referred user, so later top-ups collide with the same entry.

### P2-4 · Mini App feature parity
`IN PROGRESS` · favorites and premium purchase done; AI recommendations and watch stats/ranks remain
Favorites shipped in Phase 6 — `GET /movies/favorites`, `POST`/`DELETE /movies/{id}/favorite`, an `is_favorite` flag on every catalog card, and a Saved row on the home screen. Premium purchase shipped in Phase 5.
Still missing vs the bot: **AI recommendations** and **watch stats / ranks**.

### P2-5 · Order history
`DONE` (Phase 8)
The bot's Orders button renders real history through `app/services/payment_history.py`, the same service `GET /api/billing/history` uses — so the bot and the Mini App cannot show different money for the same user.

### P2-6 · Apply percentage-discount promos at checkout
`TODO` · `app/services/promo.py:104` · depends on P2-1
The promo type is creatable and redeemable but has no purchase to discount.

### P2-7 · Enforce or remove `auto_renew`
`TODO`
Column has zero references outside the model. Either implement renewal or drop the column — dormant fields imply behavior that does not exist.

### P2-8 · Fix or remove `monthly_orders_count`
`TODO`
Reset monthly by cron, never incremented. Same reasoning as P2-7.

---

### P2-10 · Contract the SubscriptionPlan enum
`TODO` · follow-up to Phase 4 · **do not start until the plan release is deployed everywhere**
`chp_subscriptions.plan` and `chp_payment_receipts.subscription_plan` are still written alongside the authoritative `plan_id`, so a rollback to the pre-Phase-4 release still finds what it reads. Once that is no longer a possibility, drop both columns and the `SubscriptionPlan` enum, and make `plan_id` NOT NULL.

### P2-9 · Localize the React admin panel
`TODO` · deferred from Phase 1 · **still open after Phase 6** — the two panels added there (`BroadcastPanel`, `SettingsPanel`) follow the existing hardcoded-Uzbek convention rather than introducing a second one mid-file
The panel is **entirely** hardcoded Uzbek — roughly 98 user-visible strings across 11 files in [webapp/src/admin/](webapp/src/admin/), using none of the i18n system. An earlier audit recorded it as "inconsistently localized", which understated it.
Deliberately not done in Phase 1: FR-3 rewrites this markup in Phase 6, so extracting keys now means extracting them twice. Do it as part of that redesign, against the final markup.
The bot's admin strings were localized in Phase 1 — those were only nine, and they are bot messages.

---

### P2-16 · Custom posters do not appear on bot cards
`TODO` · surfaced while fixing the gallery-upload bug
`app/bot/handlers/catalog.py` sends `title.poster_url` to Telegram, so a title whose poster is an *upload* shows TMDB's image or no image at all in the bot. Telegram fetches the URL itself, and `/api/movies/images/{id}` sits behind `initData` auth, which Telegram cannot satisfy. The Mini App is unaffected — it authenticates normally.
Two ways forward, both product/security decisions rather than bugs: serve poster images from an unauthenticated route (they are public artwork, unlike receipts), or upload the bytes to Telegram once and cache the resulting `file_id` on the title. **Not fixed here** — opening an unauthenticated endpoint is not a change to make unasked.

### P2-15 · Adopt the limiter for authenticated bursts
`TODO` · follow-up to Phase 8
The API limiter keys on the verified Telegram id where one is present and on client IP otherwise. Behind a NAT — a shared office or mobile carrier — many users collapse into one IP bucket. That is acceptable for unauthenticated paths (which are few and cheap), but if abuse is ever seen from a shared address, the fix is to require identity earlier rather than to loosen the limit.

### P2-11 · Broadcast scheduling and rich content
`Partially done` · follow-up to Phase 6 · **image/video attachment done in Phase 9E-B; interest and badge targeting done in Phase 9E-C**
Originally: broadcasts are plain text, sent immediately, to one of three audience segments. Still open: schedule for later, target by language or by last-seen date, and cancel a send in flight. None are required by any current request — recorded so the omission is deliberate rather than forgotten.

### P2-16 · A broadcast admin UI for media, translations and targeting
`Done` · Phase 9E-D
Composer, targeting, UZ/RU/EN tabs, media by `file_id`, isolated preview, backend estimate, confirmation, live progress and operator resume all shipped. Media is attached by forwarding the file to the bot and pasting the id it returns — the flow 9E-B established.

### P2-17 · Render broadcast media in the composer preview
`TODO` · follow-up to Phase 9E-D
The preview shows a labelled placeholder where the photo or video will sit, not the file itself. Telegram serves media from behind a bot token, so displaying it would mean proxying media through our backend for the sake of a thumbnail — a new authenticated route, a new cache, and a new way to leak a `file_id`. Deferred until an operator actually reports picking the wrong image; the id is captured seconds earlier in the same chat, where they can already see it.

### P2-13 · Localize collection and plan text
`TODO` · follow-up to Phase 7
Titles are now per-language; **collection names, plan names and plan benefits are not** — they are still single-language admin-authored text, and `FEATURE_REQUESTS.md` notes under FR-5 that benefits fall under FR-6. The mechanism exists and generalises: a `chp_collection_translations` shaped like the title one, read through the same fallback helper. Not built because FR-6 requirement 3 names movie titles, and the roadmap flagged the wider scope as needing confirmation.

### P2-14 · Feed translated names to the AI recommender
`TODO` · follow-up to Phase 7
`app/services/ai.py` sends the catalog to Gemini using stored names only, so a Russian-speaking user asking in Russian is matched against Uzbek titles. Including translations would improve matching, at the cost of a larger prompt for the 150-title context window.

### P2-12 · Membership check on the bot's own delivery path
`Done` · Phase 10 (2026-08-11)
Originally: `AccessMiddleware` gates every bot update except `/start`, and the Mini App gates `POST /watch` — both correct, but two enforcement points for one rule.
**The rule is now single-sourced.** [app/services/access.py](app/services/access.py) `check_title_access` is the only place the decision is made; both surfaces call it and neither re-derives it. On the bot it is invoked from `deliver_and_warn`, the one chokepoint every route to a file passes through (code search, name search, genres, collections, the episode picker), which is the practical equivalent of the `deliver_episode` suggestion above without moving user-facing messaging into the streaming service — a refusal has to be *spoken*, and returning None there would surface as "send error".
Two enforcement *call sites* remain, one per surface; that is inherent to having two surfaces. What was eliminated is two copies of the rule.

---

## P3 — Polish

- **P3-1** · Admin usage report from `ai_requests_today` / `ai_limit_reset_at` — reserved for this per `services/ai_quota.py`, currently written by nothing.
- **P3-2** · Language analytics — now that `language_selected` exists, track real preference. Legacy `UZ` rows are "unknown", not a choice.
- **P3-3** · Audio filter on the bot's browse flow — the Mini App has it; the bot does not. `browse()` already accepts the parameter.
- **P3-4** · Structured logging — currently bare `logging.basicConfig`.
- **P3-5** · Bundle size — single 216 KB JS chunk; admin panel could be lazy-loaded, since almost no users are admins.

---

## Done

### Phase 10 — Access control, trial, premium content, movie codes (2026-08-11, `1082711`)
- **One access decision** (P2-12) — [app/services/access.py](app/services/access.py) `check_title_access`, shared by the Mini App's `watch_movie` and the bot's `deliver_and_warn`. A subscription outranks channel membership; a premium title is not unlocked by joining a channel.
- **Premium-only titles** (first content benefit under P2-2) — `chp_titles.is_premium`, enforced server-side.
- **New-user trial** — granted inside the signup transaction, refused if the account ever held a subscription, off by default, configurable from the admin panel.
- **Public movie codes** — one shared `by_code` lookup behind the bot's bare-number handler and the Mini App search box. Allocated from a Postgres sequence so a deleted title's number is never reissued; the sequence is declared on the model *and* in the migration, per `CLAUDE.md` §5.
- **Fixed a pre-existing 500** on `GET /api/movies/collections` and `GET /api/movies/search/all` — `Collection.poster_image_id` existed in the database since `f6b2d94ae713` but was never declared on the model, which also meant the admin's collection-poster upload was silently discarded. No migration needed; `alembic check` confirms no drift.
- **Verification:** 802 tests passing in one serial run; tsc, build, pyflakes, `alembic check`, locale parity all clean. **Migration `f2b9c04e7a13` is not yet applied to production.**

### Phase 8 (2026-08-08)
- **Maintenance heartbeat** (P0-5, detection half) — `record_maintenance_run` / `maintenance_is_stale`, stamped by cron and checked at startup.
- **Subscription feature enforcement** (P1-a) — [app/services/plan_features.py](app/services/plan_features.py); the AI limit moved from a hardcoded premium check onto the plan.
- **Runtime visibility** (P0-6) — `/health` reports the running commit.
- **Clean-install CI gate** (P0-7) — a job installing only `requirements.txt`, plus [tests/test_runtime_dependencies.py](tests/test_runtime_dependencies.py), which caught an undeclared `starlette` import in this phase's own code.
- **REST API rate limiting** (P0-8) — [app/api/rate_limit.py](app/api/rate_limit.py), fail-open, per-verified-user, stricter on upload and delivery.
- **Order history in the bot** (P2-5) — shared with the Mini App through [app/services/payment_history.py](app/services/payment_history.py).
- **No migration.** Nothing in this phase touched the schema or production data.

### Phase 7 (2026-08-08)
- **Per-language movie titles** (FR-6 req. 3) — `chp_title_translations`, server-side resolution on every catalog read path in both surfaces, cross-language search, TMDB auto-fill for ru/en, and a translation editor in [TitleEditor.tsx](webapp/src/admin/TitleEditor.tsx).
- **Three long-open FR-6 decisions recorded** — source of translations (admin-entered + TMDB), fallback (`Title.name`, per field), language set (all three, Uzbek as an override).
- **CI's backend job fixed** — it had never passed: `BOT_TOKEN: ci-placeholder` fails aiogram's token validator at import. The check is unchanged; only the fixture is now shaped like a token.
- Migration `c8d3a51fb742` **created and rehearsed, not applied to production**.

### Phase 6 (2026-08-08)
- **Favorites in the Mini App** (P2-4, partial) — three endpoints, `is_favorite` on every card resolved in one batch query, hearts on cards and in the detail sheet, a Saved home row.
- **`is_banned` enforced** (P0-4) — `get_active_user` + `AccessMiddleware`, plus the admin toggle.
- **Referral payouts** (P2-3) — idempotent through the existing ledger index.
- **Broadcasts** — [app/services/broadcast.py](app/services/broadcast.py), `chp_broadcasts`, [BroadcastPanel.tsx](webapp/src/admin/BroadcastPanel.tsx). Sent once, paced, with delivery counts.
- **Required-channel membership** — [app/services/membership.py](app/services/membership.py) + `chp_system_settings`, configured from the panel and enforced on delivery only.
- **Receipt history filters and search** — server-side, so the search reaches past the loaded page.
- **AI quota verified** — no change needed; the Phase 5 fix is correct and now has [tests/test_ai_quota.py](tests/test_ai_quota.py) covering the rejected-request and concurrency claims.
- **Schema drift closed** — `uq_balance_history_event` existed only in migration `a3f1c92d7e04`, so it was absent from every test database. Now declared on the model too; the idempotency tests were previously running against a schema that could not enforce it.
- Migration `b2f7c1a95e30` **created and rehearsed, not yet applied to production**.

### Phase 5 (2026-08-07)
- **FR-4 · In-app purchase flow** — [app/api/billing.py](app/api/billing.py), [app/services/subscription_purchase.py](app/services/subscription_purchase.py), [PlansSheet.tsx](webapp/src/components/PlansSheet.tsx), [TopUpSheet.tsx](webapp/src/components/TopUpSheet.tsx).
- **FR-10 · Tier rules** — extend / upgrade with 1:1 carry-over / queued downgrade, all keyed on relative priority.
- **FR-11 · Receipt retention** — [app/services/images.py](app/services/images.py); 30-day purge wired into `app/tasks/cron.py`.
- **FR-12 · Admin receipt history** with search and filter.
- **Custom posters** for titles and collections.
- Migration `f6b2d94ae713` applied to production.

### Phase 4 (2026-08-07)
- **FR-5 · Database-driven subscription plans** — [app/db/models/subscription.py](app/db/models/subscription.py), [app/services/subscription_plans.py](app/services/subscription_plans.py), 11 admin endpoints, [PlansPanel.tsx](webapp/src/admin/PlansPanel.tsx). Migration `e58a3c7b91d4` applied to production; existing subscription and receipt repointed with terms unchanged.

### Phase 3 (2026-08-07)
- **FR-1 · Super Admin & granular permissions** — 19 capabilities in [app/core/permissions.py](app/core/permissions.py); single enforcement in [app/services/permissions.py](app/services/permissions.py) used by both the API and the bot; `app/core/admin.py` deleted. Migrations `c41d5b8ae902` + `d72e4f1c8b35` applied to production.
- **Admin management** — REST endpoints plus [AdminsPanel.tsx](webapp/src/admin/AdminsPanel.tsx); permission vocabulary served by the backend so the panel cannot drift.
- **Auto-delete removed** — engine, worker, config, notice, and locale key, at the owner's direction.
- **Playback no longer forced to episode 1** — the sheet's generic Watch button is hidden whenever an episode chooser is shown.

### Phase 2 (2026-08-07)
- **FR-9 · Episode and season navigation** — viewer-facing `GET /movies/{id}/seasons` and `/episodes` (both existed in the service layer but were reachable only via `/api/admin`); `POST /watch` accepts an optional `episode_id`, keeping older clients working; new [EpisodeSelector.tsx](webapp/src/components/EpisodeSelector.tsx) with a season strip, paged episode list and infinite scroll. Ends "every serial plays episode 1".
- **FR-7 · Audio languages before playback** — per-episode audio badges resolved in one batch query, using labels already in the catalogs. Informational by design; selection recorded as **I-13** in `IDEAS.md`.
- **Regression fixed** — the `date` import removed during Phase 0's review broke OpenAPI generation (and `/docs`) while leaving `import app.main` green. Restored, annotated, and covered by [tests/test_api_schema.py](tests/test_api_schema.py).
- **P3-3 partially superseded** — the bot already had season/episode navigation; this closes the gap on the Mini App side.

### Phase 1 (2026-08-05)
- **FR-6 reqs 1–2 · Interface and system text localization** — `POST /movies/{id}/watch` no longer returns hardcoded English (confirmation *and* errors, both rendered verbatim as toasts by the Mini App); nine hardcoded Uzbek strings in the bot's admin flows moved to the catalogs; theme-toggle `aria-label`s translated. 11 new keys, 196 total, parity verified.
- **FR-6 side-finding · Information disclosure** — the watch endpoint returned `detail=str(exc)`, an internal diagnostic naming a `MediaFile` row, straight to the user's screen. Now logged server-side with a translated message returned instead.
- **FR-8 · `/start` reliability** — the language picker is gated on `language_selected` rather than row age, so a user who never answered is asked again instead of being silently left on the Uzbek default; `get_or_create_user` refreshes `username`/`full_name`, which had been written once at signup and never updated.
- **P0-1 follow-through** — removed the last pre-existing unused import in [app/api/movies.py](app/api/movies.py), flagged during the Phase 0 review.

- **Mini App i18n** — `GET /api/i18n/{lang}`, `lib/i18n.ts` + React context, all hardcoded strings replaced across `App.tsx` and `components/`. (`9bd6d48`, 2026-08-05)
- **First-open language picker** + `PATCH /api/auth/me` accepting `{language}`, with `language_selected` migration backfilling 508 existing users. (`9bd6d48`, 2026-08-05)
- **Mini App settings page** — name, Telegram ID, balance, premium status, referral code with copy, language switcher. (`9bd6d48`, 2026-08-05)
- **Audio-language filter** — `_has_playable_file(audio_language)` reused across all six catalog endpoints, plus `AudioFilter` UI. (`9bd6d48` + follow-ups, 2026-08-05)
- **Migration `6b7ec8ebd218` applied to production** — column verified, 508/508 users backfilled to `true`. (2026-08-05)
