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

### Catalog queries
- "Is this title watchable?" is answered by exactly one helper: `content._has_playable_file(audio_language)`, a correlated `EXISTS`. **Reuse it — never add a parallel join.** Filtering audio language narrows that same subquery, so "has a file" and "has a Russian file" stay one question.
- Admin listings deliberately skip this gate: surfacing incomplete titles is what that screen is for.

### Auth
- Mini App identity comes only from Telegram `initData` HMAC verification (`app/api/auth.py`). Never trust a `telegram_id` from a query param or body — it is trivially spoofable.
- Admin routes carry **two** gates: the router-wide `get_current_admin` (is this an administrator at all) and a per-route `require_permission(Permission.X)` (may they do *this*). Keep both — the blanket gate is what stops a newly added route being accidentally public.
- **There is exactly one place authority is decided:** `app.services.permissions.has_permission`. The REST API reaches it through `require_permission`; the bot through `app/bot/permissions.py`. Never add a second check that reads roles or `ADMIN_IDS` directly — `ADMIN_IDS` is a legacy seed and grants nothing at runtime.
- The Super Admin (`SUPER_ADMIN_TELEGRAM_ID`) holds every permission implicitly and has no rows in `chp_admin_permissions`. Do not seed them — a revocable grant could lock the platform out of itself.

### Money
- Balance credits, subscription activation, and user notification happen in **one place**: `app/services/payment_review.py`. The bot's inline approve/reject buttons and the admin REST API both call it. Do not add a second crediting path.

---

## 4. Conventions

- **Comments explain *why*, not *what*.** This codebase's comments document the reasoning behind a non-obvious choice — the failure mode avoided, the alternative rejected. Match that register; don't add narration.
- All tables are prefixed `chp_`.
- Services are singletons instantiated at module bottom (`content_service = ContentService()`).
- Read paths and admin write paths are separate modules by design: `services/content.py` (viewer-facing) vs `services/admin_content.py` (mutations). Keep them apart.
- Frontend: functional components, Tailwind utility classes, semantic color tokens (`ink`, `surface`, `marquee`) — not raw palette values. Both light and dark themes must work.

---

## 5. Known traps

- `app/tasks/cron.py` is a standalone script, intentionally not bolted onto the web lifespan. It only runs if a Render Cron Job is configured — that config is **not in this repo**, so verify externally before assuming maintenance tasks run.
- The webhook is set on startup and deliberately **never deleted on shutdown** — it is global bot state, not one process's to release.
- `pyflakes` reports `date` in `app/api/admin.py` as unused. It is **not** — `ActivityPointOut.date: date` shadows it, and removing it breaks OpenAPI generation while leaving `import app.main` green. It was deleted once on that advice and shipped broken; `tests/test_api_schema.py` now catches it.
- Delivered videos are **never** auto-removed. The 15-minute auto-delete engine was removed in Phase 3 — do not reintroduce timers, Redis delay queues, or deletion notices.
