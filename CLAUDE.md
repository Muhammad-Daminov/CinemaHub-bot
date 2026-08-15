# CLAUDE.md

Operating guide for AI assistants working in this repository.
Read this file first, every session.

---

## 0. Documentation protocol (mandatory)

Five files in the project root are the **single source of truth**:

| File | Holds | Update when |
|---|---|---|
| `CLAUDE.md` | How to work in this repo | Conventions or commands change |
| `PROJECT_CONTEXT.md` | Architecture, data model, feature inventory | Any feature ships or its status changes |
| `TASKS.md` | Prioritized backlog with status | A task is started, finished, or discovered |
| `IDEAS.md` | Discussed-but-unbuilt ideas | An idea is raised, promoted to a task, or rejected |
| `CHANGELOG.md` | Shipped, user-visible history | A change is committed |

**Workflow for every feature request:**

1. **Read** `PROJECT_CONTEXT.md` + `TASKS.md` + `IDEAS.md` before proposing anything.
2. **Check** whether the request is already built, partially built, or listed as an idea. Say which.
3. **Implement** only after that check.
4. **Update** the affected files in the same session — move the item out of `IDEAS.md`, mark it in `TASKS.md`, adjust `PROJECT_CONTEXT.md`, add a `CHANGELOG.md` entry under `[Unreleased]`.
5. **Never delete project knowledge.** Supersede it: mark items `Done` / `Rejected (reason)` / `Superseded by X` rather than removing lines.

If a code change contradicts these docs, the code wins — fix the doc in the same commit.

---

## 1. What this is

**CinemaHub Pro** — a Telegram-first film/serial streaming service for an Uzbek-speaking audience. Two front ends over one FastAPI backend and one Postgres database:

- an **aiogram 3 bot** (browse, watch, pay, AI recommendations), and
- a **React Mini App** served from the same process at `/miniapp`, which also contains the **admin panel**.

Full detail in `PROJECT_CONTEXT.md`.

---

## 2. Commands

```bash
# Backend
python -c "import app.main"                 # fastest sanity check — catches import/wiring breaks
uvicorn app.main:app --reload               # run API + bot webhook locally
alembic upgrade head                        # apply migrations (see warning below)
alembic revision --autogenerate -m "msg"    # new migration
python -m app.tasks.cron                    # run scheduled maintenance once

# Tests
./scripts/test_db.sh start                  # throwaway local Postgres (no sudo); prints the URL to export
python -m pytest -q                          # DB-backed tests skip unless TEST_DATABASE_URL is set
python scripts/check_locales.py              # locale parity + every used key resolves

# Frontend (from webapp/)
npm run build                               # tsc -b && vite build — the real check
npx tsc --noEmit                            # typecheck only, faster
npm run dev                                 # vite dev server
```

**Four gates, all of which must pass** before declaring work done: `python -c "import app.main"`, `python -m pytest -q`, `python scripts/check_locales.py`, and `npm run build`.

`import app.main` alone is not enough — Pydantic resolves response-model annotations lazily, so a broken one passes the import check and fails only when the OpenAPI schema is built. `tests/test_api_schema.py` covers that.

Database-backed tests skip silently without `TEST_DATABASE_URL`, so a green run proves less than it looks like. Start `./scripts/test_db.sh` and export the URL it prints. `tests/conftest.py` refuses any Neon host outright — the suite drops and recreates every table.

---

## 3. Hard rules

### Database
- **`DATABASE_URL` points at a live production Neon database.** `scripts/test_db.sh` provides a throwaway cluster for *tests*, but there is still no staging database — rehearse migrations there before applying them. `alembic upgrade head`, any `UPDATE`, and any destructive SQL hit production.
- **Always confirm with the user before running a migration or any write.** State that it targets production.
- Migrations that add a `NOT NULL` column must carry a `server_default` **and** backfill existing rows explicitly (see `6b7ec8ebd218` for the pattern).

### Async SQLAlchemy
- **Never rely on lazy relationship loading** — it raises `MissingGreenlet` under async. Use explicit `select()` + `join()`, or `selectinload()`.
- Deletes go child-first using Core `delete()`, not ORM cascade (cascade needs the relationship loaded).

### i18n — one catalog, two surfaces
- `app/locales/{uz,ru,en}.json` are the **only** place UI strings live. The bot reads them via `app.core.i18n.t`; the Mini App fetches the same files from `GET /api/i18n/{lang}`.
- **Never hardcode a user-facing string** in a bot handler or a React component. Add a key to all three locales.
- `uz` is the fallback language. The backend merges the requested language over `uz` before sending, so the frontend only ever handles a key missing from every locale (it renders the key itself — a visible `app.foo` is an intentional bug report).
- `chp_users.language` is shared: changing it in the Mini App also changes the bot's replies. That is the design.

### Catalog text
- **A title has one stored name and any number of translations.** `Title.name` is authoritative and is the fallback — it holds the Uzbek name the catalog is indexed by, which is why `apply_tmdb_match` refuses to overwrite it. Per-language values live in `chp_title_translations`.
- **Resolve through `content_service.localized_titles`, never by reading `Title.name` at a render site.** It batches one query per page; a lookup per card is the N+1 this codebase keeps having to remove. `deliver_episode` resolves the caption itself, so the bot and the Mini App cannot disagree.
- **Search matches every language, not the viewer's.** Reuse `_title_name_matches` — it is an EXISTS, so a title with three translations still appears once, and a join here would return it three times.
- TMDB auto-fill writes `source = TMDB` and **never overwrites a `MANUAL` row**. That distinction is the only thing protecting an administrator's correction from the next enrichment.

### Catalog queries
- "Is this title watchable?" is answered by exactly one helper: `content._has_playable_file(audio_language)`, a correlated `EXISTS`. **Reuse it — never add a parallel join.** Filtering audio language narrows that same subquery, so "has a file" and "has a Russian file" stay one question.
- Admin listings deliberately skip this gate: surfacing incomplete titles is what that screen is for.

### Auth
- Mini App identity comes only from Telegram `initData` HMAC verification (`app/api/auth.py`). Never trust a `telegram_id` from a query param or body — it is trivially spoofable.
- Admin routes carry **two** gates: the router-wide `get_current_admin` (is this an administrator at all) and a per-route `require_permission(Permission.X)` (may they do *this*). Keep both — the blanket gate is what stops a newly added route being accidentally public.
- **There is exactly one place authority is decided:** `app.services.permissions.has_permission`. The REST API reaches it through `require_permission`; the bot through `app/bot/permissions.py`. Never add a second check that reads roles or `ADMIN_IDS` directly — `ADMIN_IDS` is a legacy seed and grants nothing at runtime.
- The Super Admin (`SUPER_ADMIN_TELEGRAM_ID`) holds every permission implicitly and has no rows in `chp_admin_permissions`. Do not seed them — a revocable grant could lock the platform out of itself.

### Auth (continued)
- **Identification and permission to act are different questions.** `get_current_user` identifies; `get_active_user` also refuses a banned account and is what every route that *does* something depends on. `/api/auth/me` deliberately keeps using the former — it is how the Mini App learns it is blocked instead of merely appearing empty. The Super Admin is exempt from bans; a platform whose owner can be locked out of it is a platform that can be taken hostage.
- The bot's equivalent is `AccessMiddleware`. `/start` and the membership-recheck callback pass through it by design; everything else does not.

### Entitlements
- **What a subscription *includes* is decided in exactly one place:** `app.services.plan_features.features_for_user`, the entitlement counterpart to `has_permission`. Never re-derive a benefit from `is_user_premium` — that is the hardcoded rule this replaced, and a second path would eventually refuse a paying user something they bought.
- A feature absent from a plan falls back to the previous behaviour rather than to "denied". A grant with no value means "no cap"; an unparseable value falls back rather than guessing a number, because a typo in the admin panel must not cut off paying users.

### Money
- Balance credits, subscription activation, and user notification happen in **one place**: `app/services/payment_review.py`. The bot's inline approve/reject buttons and the admin REST API both call it. Do not add a second crediting path.
- **A credit that must happen once is guarded by the database, never by a read.** `if already_paid: return` is a race, and this project has already lost money to that exact shape. Insert the `chp_balance_history` row with `ON CONFLICT DO NOTHING` against `uq_balance_history_event` and move the balance only if a row was actually created — see `app/services/referral.py`.
- Referral payouts fire from `approve_receipt` inside the same transaction as the credit. A bonus that survived a rolled-back approval would be money created from a payment that never happened.

### Broadcasts and platform settings
- **A broadcast is claimed, not started.** `run_broadcast` moves the row `PENDING → SENDING` under `SELECT … FOR UPDATE` and stops if someone else got there first. Never send from a request handler: the request's transaction would stay open for minutes, and a retried request would be a second send to every user.
- **The membership check fails open.** If Telegram errors, the channel is missing, or the bot is not an administrator there, the user is let through and it is logged. Closing on failure turns one misconfiguration into a total outage with no visible cause.
- `chp_system_settings` is read and written only through `app/services/settings_store.py`, which owns the key constants and their parsing. A setting addressed by a string literal elsewhere reads as "unset" when misspelled — and for a gate, unset means open.

---

## 4. Conventions

- **Comments explain *why*, not *what*.** This codebase's comments document the reasoning behind a non-obvious choice — the failure mode avoided, the alternative rejected. Match that register; don't add narration.
- All tables are prefixed `chp_`.
- Services are singletons instantiated at module bottom (`content_service = ContentService()`).
- Read paths and admin write paths are separate modules by design: `services/content.py` (viewer-facing) vs `services/admin_content.py` (mutations). Keep them apart.
- Frontend: functional components, Tailwind utility classes, semantic color tokens (`ink`, `surface`, `marquee`) — not raw palette values. Both light and dark themes must work.

---

### Operations
- **`requirements.txt` is what production installs.** `requirements-dev.txt` is a superset, so a runtime import missing from the former passes every local gate and crashes only on Render — which then keeps serving the *previous* build, silently. `tests/test_runtime_dependencies.py` and the `runtime-deps` CI job both guard this; do not import a package (`starlette`, say) that arrives only transitively.
- **The API limiter fails open.** If Redis is unreachable the request is served. Never "fix" that to deny — it converts a cache blip into a total outage.
- **`/health`'s `status` field is a contract** read by Render's health check and an uptime monitor. Add fields freely; do not change that one.
- **Scheduled maintenance reports itself.** `app/tasks/cron.py` stamps `last_maintenance_run` on completion and startup warns when it is stale. If you see `SCHEDULED MAINTENANCE OVERDUE` in the logs, **no scheduler is running the job** — receipt images are not being purged. Expect that warning until the VPS migration lands; the scheduler is being configured there rather than on Render (TASKS.md P0-5).

---

## 5. Known traps

- `app/tasks/cron.py` is a standalone script, intentionally not bolted onto the web lifespan. It only runs if something external schedules it, and that scheduler config is **not in this repo** — so verify externally before assuming maintenance tasks run. It is deliberately scheduler-agnostic: `cron`, a `systemd` timer or a hosted cron all work, since it takes its config from the environment and reports success through its exit code.
- The webhook is set on startup and deliberately **never deleted on shutdown** — it is global bot state, not one process's to release.
- `pyflakes` reports `date` in `app/api/admin.py` as unused. It is **not** — `ActivityPointOut.date: date` shadows it, and removing it breaks OpenAPI generation while leaving `import app.main` green. It was deleted once on that advice and shipped broken; `tests/test_api_schema.py` now catches it.
- **An index that exists only in a migration does not exist under test.** The suite builds its schema with `metadata.create_all` from the models, so `uq_balance_history_event` — declared in migration `a3f1c92d7e04` alone — was absent from every test database, and the tests asserting "this credit cannot happen twice" were passing against a schema that could not enforce it. It is now on the model too. Any constraint carrying a correctness guarantee must be declared in both places.
- Delivered videos are **never** auto-removed. The 15-minute auto-delete engine was removed in Phase 3 — do not reintroduce timers, Redis delay queues, or deletion notices.
---

## 6. Versioning and releases (mandatory)

**The root `VERSION` file is the single source of truth.** Nothing else
carries a release number. `app/core/version.py` reads it for `/health`'s
`app_version`; `webapp/vite.config.ts` bakes it into the Mini App bundle
as `__APP_VERSION__`, shown under Settings → Ilova. Never introduce a
second version — `webapp/package.json` had a stale `0.1.0` that nothing
read, and it was removed rather than kept in sync, because two numbers
free to drift are worse than one.

`/health` reports both: `version` is **build identity** (the short commit,
unchanged and possibly read by external systems) and `app_version` is the
**release**. Comparing them is how a deploy is confirmed.

### Bump on every release-worthy change, without being asked

This is not something to wait for a request on. When a change set is ready
to commit, decide its bump, update `VERSION`, write the `CHANGELOG.md`
entry, and tag — **all in the same commit as the code**.

| Bump | For this project |
|---|---|
| **MAJOR** | Breaking API, schema or contract change needing a coordinated deploy: removing an endpoint, changing a response shape the Mini App reads, a migration not backwards compatible with the running build. |
| **MINOR** | A new backwards-compatible capability, viewer- or admin-facing. |
| **PATCH** | Bug fixes, performance work, security fixes — any backwards-compatible correction. |
| **No bump** | Docs-only, comments-only, CI-only, and intermediate commits inside one change set. |

**One logical change set is one bump**, however many files it touches. A
feature spanning twelve files is a single MINOR, not twelve PATCHes. If a
set contains both a feature and a fix, the higher bump wins.

**If the right bump is genuinely unclear, stop and explain the reasoning
rather than silently picking one.**

### Release mechanics

- Tag every release `vMAJOR.MINOR.PATCH`, annotated, on the release commit.
- Never make a standalone "update version" commit. The version bump, the
  changelog entry and the code are one release and belong together.
- The commit message should make the release understandable on its own.
- `CHANGELOG.md` uses version headings (`## [1.1.0] — date`) with
  `[Unreleased]` for work in flight. Entries below `1.0.0` predate
  versioning and keep their original date-and-commit grouping — as
  everywhere else here, history is superseded, never deleted.
