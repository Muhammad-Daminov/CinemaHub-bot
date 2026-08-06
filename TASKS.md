# TASKS.md

Prioritized backlog. **Status of record** for what to work on next.

**Status:** `TODO` · `IN PROGRESS` · `BLOCKED` · `DONE` · `WON'T DO`
**Priority:** P0 correctness/risk · P1 foundation · P2 product value · P3 polish

Finished work moves to §Done (kept, not deleted) and gets a `CHANGELOG.md` entry.

---

## P0 — Correctness & risk

### P0-1 · Duplicate `GEMINI_MODEL` in config
`TODO` · `app/core/config.py` (~L46, ~L48)
Declared twice; the second (`gemini-2.5-flash`) silently wins. Anyone editing the first sees no effect. Delete the stale line and confirm the intended model.
**Effort:** minutes.

### P0-2 · Subscription receipts credit balance *and* grant premium
`TODO` · `app/services/payment_review.py:35`
`user.balance += receipt.amount` runs unconditionally, then a `SUBSCRIPTION`-purpose receipt *also* activates premium. A user paying 50,000 for premium receives premium **plus** 50,000 in balance.
Not currently exploitable — balance cannot be spent (see P2-1) — but it becomes a live double-credit the moment a spending path exists, and the ledger is already wrong today.
**Decide:** should a subscription receipt credit balance at all? Likely fix is to credit only for `PaymentPurpose.TOPUP`.
**Blocks:** P2-1.

### P0-3 · No staging or local database
`TODO` · infrastructure
Every migration and every manual query hits production. Cheapest fix: a **Neon branch** as a dev database, with `DATABASE_URL` switched by environment.
**Effort:** ~1h. **High leverage** — de-risks everything below.

### P0-4 · `is_banned` is not enforced
`TODO` · `app/bot/middlewares/`, `app/api/auth.py`
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
`TODO` · **blocked by P0-2**
Users can top up but cannot spend. `DEDUCTION`/`REFUND` tx types are unused. Decide what balance buys — most naturally, premium itself (removing the second receipt round trip). Until then, top-up is a dead end that takes money and returns a number.

### P2-2 · Give premium real benefits
`TODO`
Premium's only current benefit is unlimited AI recommendations. Options: skip the 15-min auto-delete window, higher-quality files, early access, ad-free, larger favorites cap. **Product decision needed before implementation.**

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

## P3 — Polish

- **P3-1** · Admin usage report from `ai_requests_today` / `ai_limit_reset_at` — reserved for this per `services/ai_quota.py`, currently written by nothing.
- **P3-2** · Language analytics — now that `language_selected` exists, track real preference. Legacy `UZ` rows are "unknown", not a choice.
- **P3-3** · Audio filter on the bot's browse flow — the Mini App has it; the bot does not. `browse()` already accepts the parameter.
- **P3-4** · Structured logging — currently bare `logging.basicConfig`.
- **P3-5** · Bundle size — single 216 KB JS chunk; admin panel could be lazy-loaded, since almost no users are admins.

---

## Done

- **Mini App i18n** — `GET /api/i18n/{lang}`, `lib/i18n.ts` + React context, all hardcoded strings replaced across `App.tsx` and `components/`. (`9bd6d48`, 2026-08-05)
- **First-open language picker** + `PATCH /api/auth/me` accepting `{language}`, with `language_selected` migration backfilling 508 existing users. (`9bd6d48`, 2026-08-05)
- **Mini App settings page** — name, Telegram ID, balance, premium status, referral code with copy, language switcher. (`9bd6d48`, 2026-08-05)
- **Audio-language filter** — `_has_playable_file(audio_language)` reused across all six catalog endpoints, plus `AudioFilter` UI. (`9bd6d48` + follow-ups, 2026-08-05)
- **Migration `6b7ec8ebd218` applied to production** — column verified, 508/508 users backfilled to `true`. (2026-08-05)
