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
`TODO` · unchanged by Phase 3 — the role system governs *administrators*; banning *users* is still unenforced and has no setter · `app/bot/middlewares/`, `app/api/auth.py`
The column exists and the admin panel displays it, but nothing checks it and no endpoint sets it. There is currently **no way to remove an abusive user**. Needs enforcement in bot middleware + `get_current_user`, and an admin toggle endpoint.

### P0-5 · Verify scheduled maintenance actually runs
`TODO` · ops
`app/tasks/cron.py` is correct and idempotent but is invoked by nothing in this repo. If no Render Cron Job exists, stale receipts are never expired and expired promos stay active. Confirm in the Render dashboard; document the result in `PROJECT_CONTEXT.md`.

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
Add `render.yaml` (web service + cron job) so infrastructure is reviewable and reproducible.

### P1-8 · Stop committing `tsconfig.tsbuildinfo`
`TODO` · `.gitignore`
Build artifact; churns on every build and pollutes diffs.

---

## P2 — Product value

### P2-1 · Make balance spendable
`DONE` (Phase 5) — subscriptions are purchasable from balance in the Mini App
Users can top up but cannot spend. `DEDUCTION`/`REFUND` tx types are unused. Decide what balance buys — most naturally, premium itself (removing the second receipt round trip). Until then, top-up is a dead end that takes money and returns a number.

### P2-2 · Give premium real benefits
`TODO`
Premium's only current benefit is unlimited AI recommendations. Options: higher-quality files, early access, ad-free, larger favorites cap. (The "skip auto-delete" option is gone — auto-delete was removed entirely in Phase 3.) **Product decision needed before implementation.**

### P2-3 · Pay out referral rewards
`TODO`
Capture works; nothing is ever granted. The Mini App actively promotes a code that does nothing. Options: credit both parties on the referee's first approved payment (guards against self-referral farming), or grant premium days. Needs `BalanceTxType` plumbing and a new i18n key set.

### P2-4 · Mini App feature parity
`TODO`
Missing vs the bot: favorites, AI recommendations, premium purchase, watch stats/ranks. Favorites is the cheapest and most-expected — the API and service layer already exist (`toggle_favorite`, `list_favorites`).

### P2-5 · Order history
`TODO` · `app/bot/handlers/base.py:147`
Currently a "coming in a later phase" stub. Data already exists in `chp_payment_receipts` + `chp_balance_history`.

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
`TODO` · deferred from Phase 1 · **schedule with FR-3 (Phase 6)**
The panel is **entirely** hardcoded Uzbek — roughly 98 user-visible strings across 11 files in [webapp/src/admin/](webapp/src/admin/), using none of the i18n system. An earlier audit recorded it as "inconsistently localized", which understated it.
Deliberately not done in Phase 1: FR-3 rewrites this markup in Phase 6, so extracting keys now means extracting them twice. Do it as part of that redesign, against the final markup.
The bot's admin strings were localized in Phase 1 — those were only nine, and they are bot messages.

---

## P3 — Polish

- **P3-1** · Admin usage report from `ai_requests_today` / `ai_limit_reset_at` — reserved for this per `services/ai_quota.py`, currently written by nothing.
- **P3-2** · Language analytics — now that `language_selected` exists, track real preference. Legacy `UZ` rows are "unknown", not a choice.
- **P3-3** · Audio filter on the bot's browse flow — the Mini App has it; the bot does not. `browse()` already accepts the parameter.
- **P3-4** · Structured logging — currently bare `logging.basicConfig`.
- **P3-5** · Bundle size — single 216 KB JS chunk; admin panel could be lazy-loaded, since almost no users are admins.

---

## Done

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
