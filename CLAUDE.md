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

# Frontend (from webapp/)
npm run build                               # tsc -b && vite build — the real check
npx tsc --noEmit                            # typecheck only, faster
npm run dev                                 # vite dev server
```

**There is no test suite.** `python -c "import app.main"` and `npm run build` are the current verification gates. Run both before declaring backend or frontend work done.

### Locale parity check

After touching `app/locales/*.json` or any `t(...)` call, verify all three catalogs agree and every used key resolves. See `TASKS.md` P1 for scripting this properly; until then, check that `uz.json`, `ru.json`, and `en.json` have identical key sets.

---

## 3. Hard rules

### Database
- **`DATABASE_URL` points at a live production Neon database.** There is no local or staging database configured. `alembic upgrade head`, any `UPDATE`, and any destructive SQL hit production.
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
- Admin routes go through `get_current_admin`, which checks `ADMIN_IDS`.

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

- `app/core/config.py` declares **`GEMINI_MODEL` twice** (lines ~46 and ~48). The second wins. See `TASKS.md` P0.
- `app/tasks/cron.py` is a standalone script, intentionally not bolted onto the web lifespan. It only runs if a Render Cron Job is configured — that config is **not in this repo**, so verify externally before assuming maintenance tasks run.
- The webhook is set on startup and deliberately **never deleted on shutdown** — it is global bot state, not one process's to release.
- `webapp/tsconfig.tsbuildinfo` is committed and churns on every build. Noise in diffs; see `TASKS.md`.
