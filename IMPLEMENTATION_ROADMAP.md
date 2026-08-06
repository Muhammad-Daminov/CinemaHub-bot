# Implementation Roadmap

Execution plan synthesising `FEATURE_REQUESTS.md` (what is wanted), `IMPLEMENTATION_STATUS.md` (what exists), and `TASKS.md` (what is already scheduled).

Phases are ordered by **dependency**, not by feature number. Work is grouped so that each database migration, each shared UI surface, and each authorization decision is handled **once**.

_Created 2026-08-05 · commit `9bd6d48` · no code modified_

---

## Design Principles Applied

| Constraint | How this plan satisfies it |
|---|---|
| **Minimise migrations** | Four migrations total across nine features — Phases 0, 3, 4, 7. Reducible to **three** if the Phase 0 fix ships as a row lock alone. Permission storage (FR-1) and plan storage (FR-5) can collapse into one migration if their open decisions land together — see Phase 4. |
| **Minimise UI rewrites** | FR-7 and FR-9 both target the film detail sheet, so they share Phase 2 rather than touching it twice. FR-3 is deliberately **last**, because FR-1, FR-2 and FR-5 each add admin interface to the surface it would redesign. |
| **Minimise duplicated work** | FR-1, FR-2 and P0-4 all modify the authorization core, so they share Phase 3 — the core is designed once, knowing all three requirements. |
| **Group related work** | Phases map to domains — playback, identity, commerce, admin — not to the request list. |
| **Dependency ordering** | Every blocker is cleared in Phase 0. Phases 1 and 2 are independent of all blockers and could start today. |

---

## Phase 0 · Stabilise, Unblock, Instrument

**Goal**
Stop the one defect actively corrupting production data, create somewhere safe to test schema changes, and put a verification net in place before the larger phases begin.

**Features included**
`TASKS.md` P0-2 (receipt approval race), P0-3 (non-production database), P0-1 (duplicate config field), P0-5 (verify cron runs), P1-2/P1-3 (CI + locale parity), P1-8 (build artefact in git). No `FEATURE_REQUESTS.md` items.

**Why this phase comes now**
Two independent reasons, either sufficient on its own. First, P0-2 is not theoretical — production holds five ledger rows and five subscriptions generated from a single receipt, and the ledger is wrong today. Second, every later phase carries a migration, and there is currently **no environment to test a migration against**; `DATABASE_URL` is production. Doing anything schema-shaped before P0-3 means rehearsing on live user data.

**Dependencies**
None. This phase is the root of the graph.

**Files likely to change**
[app/services/payment_review.py](app/services/payment_review.py), [app/core/config.py](app/core/config.py), [alembic/versions/](alembic/versions/), `.github/workflows/` (new), `scripts/check_locales.py` (new), [.gitignore](.gitignore), `render.yaml` (new, optional)

**Database changes**
**One migration, optional.** A uniqueness constraint on the balance ledger's idempotency key (`reference_id` scoped to transaction type) as a database-level backstop against duplicate credits. *The race itself is fixed in code by row locking* — if you prefer zero migrations here, the constraint can batch into Phase 3. Shipping it now is the stronger guarantee.

Separately, a **production data cleanup** is required: four duplicate ledger rows and four duplicate subscriptions, one of which is granting a user an extra 30 days of premium. This is a write to production and needs explicit approval with the statements shown first.

**Backend tasks**
1. Lock the receipt row for update before the status guard in `approve_receipt`, so concurrent approvals serialise instead of all observing `PENDING`.
2. Make the balance credit safe against lost updates — the current read-modify-write is what caused five credits to collapse into one.
3. Decide and apply the FR-adjacent question already open in `TASKS.md` P0-2: whether a `SUBSCRIPTION`-purpose receipt should credit balance at all. Recommended: credit only for `TOPUP`.
4. Remove the duplicate `GEMINI_MODEL` declaration.
5. Confirm externally whether a Render Cron Job invokes `app/tasks/cron.py`; record the answer in `PROJECT_CONTEXT.md`.
6. Provision a Neon branch as a development database; switch `DATABASE_URL` by environment.
7. Bootstrap the test harness (pytest + pytest-asyncio) and cover the approval path first.
8. CI running import check, frontend build, locale parity, and tests.

**Frontend tasks**
None beyond adding the build artefact to `.gitignore`.

**Risks**
- The production cleanup is irreversible; take a Neon branch snapshot first.
- Row locking changes transaction behaviour under concurrency — the area with no test coverage today, which is why the harness lands in the same phase.
- Cron may turn out never to have run, meaning stale receipts and expired promos have accumulated. Treat that as a finding, not a surprise.

**Estimated complexity — M.** Individually small changes, but touching money and production data, and gated on external infrastructure setup.

---

## Phase 1 · Correctness Quick Wins

**Goal**
Fix defects users encounter today, at the lowest possible cost and with no schema change.

**Features included**
FR-6 requirements 1–2 (interface and system text localization), FR-8 (`/start` reliability).

**Why this phase comes now**
Both are confirmed or near-confirmed defects rather than enhancements, neither requires a migration, and neither depends on any other phase. FR-6's cited failure is located and reproducible: [app/api/movies.py:263](app/api/movies.py#L263) returns a hardcoded English sentence in the API response, so every user sees it in English regardless of their language.

FR-6 is deliberately **split**: requirements 1–2 are a contained text fix and ship here; requirement 3 (per-language movie titles) is a schema project and is deferred to Phase 7. Coupling them would hold a small fix behind a large one.

**Dependencies**
The locale parity script from Phase 0 makes the text sweep exhaustive rather than best-effort. FR-8 depends on a **reproduction case from the requester** — it is the only item in the backlog where the problem, not the solution, is undefined.

**Files likely to change**
[app/api/movies.py](app/api/movies.py), [app/locales/uz.json](app/locales/uz.json), [app/locales/ru.json](app/locales/ru.json), [app/locales/en.json](app/locales/en.json), [webapp/src/components/ThemeToggle.tsx](webapp/src/components/ThemeToggle.tsx), [webapp/src/admin/UploadsPanel.tsx](webapp/src/admin/UploadsPanel.tsx), [app/bot/handlers/base.py](app/bot/handlers/base.py)

**Database changes**
None.

**Backend tasks**
1. Move `movies.py:263` into the locale catalogs; return a key or a translated string resolved from the user's language.
2. Sweep for remaining hardcoded user-facing strings using the Phase 0 parity script.
3. FR-8: once the failing case is supplied, fix it. If no reproduction is available, the most likely reading is a user who receives the language picker, never answers, and on the next `/start` counts as existing — reaching the main menu having never chosen a language, and never being asked again.

**Frontend tasks**
1. Replace hardcoded aria-labels in `ThemeToggle.tsx`.
2. Replace hardcoded Uzbek in `UploadsPanel.tsx`.
3. Decide whether the admin panel is in scope for localization; it is currently inconsistent.

**Risks**
- Low. The main risk is scope creep from "every text must respect the language" into a full admin-panel localization project — decide that boundary explicitly before starting.
- Fixing FR-8 without a reproduction risks changing behaviour that was never broken.

**Estimated complexity — S.**

---

## Phase 2 · Playback & Series Experience

**Goal**
Make the pre-playback surface useful: show what a title offers, and let the viewer choose which episode to watch.

**Features included**
FR-7 (audio languages before playback), FR-9 (episode and season navigation).

**Why this phase comes now**
These two are grouped **specifically to avoid rewriting the same UI twice** — both add information to the film detail sheet, and FR-7's display becomes per-episode the moment FR-9 introduces episode selection. Splitting them would mean designing that sheet, then redesigning it.

The phase is also cheap relative to its impact, because the backend largely exists. `content_service.available_languages()` was written for exactly this purpose and **is called from nowhere** ([content.py:421](app/services/content.py#L421)); `list_seasons` and `list_episodes` exist but are exposed only through admin routes. The single worst behaviour in the product is here and is one line of intent: [movies.py:243](app/api/movies.py#L243) — *"The Mini App lists titles, not episodes — deliver the first one."* Pressing Watch on any serial always sends episode 1.

**Dependencies**
None blocking. Audio-language labels already exist in all three locales, so this benefits from Phase 1's conventions but does not require them.

**Files likely to change**
[app/api/movies.py](app/api/movies.py), [app/services/content.py](app/services/content.py), [webapp/src/components/MovieDetailSheet.tsx](webapp/src/components/MovieDetailSheet.tsx), [webapp/src/lib/api.ts](webapp/src/lib/api.ts), [webapp/src/types/movie.ts](webapp/src/types/movie.ts), [app/locales/](app/locales/)

**Database changes**
None. The model already carries `season`, `number`, and per-file `language`.

**Backend tasks**
1. Public endpoint exposing `available_languages` for a title or episode.
2. Public `list_seasons` and `list_episodes` endpoints on the viewer-facing router — not the admin one.
3. **Change `POST /movies/{id}/watch` to accept an episode identifier**, defaulting to the current behaviour only when none is supplied. This single change ends "always plays episode 1" before any UI exists.
4. Optionally return watched state per episode; `chp_watch_history` already holds it.

**Frontend tasks**
1. Render available audio languages in the detail sheet using the existing `audio.*` locale keys.
2. Season selector and episode list in the detail sheet.
3. Progressive loading for long episode lists.
4. Pass the chosen episode to the watch call.

**Risks**
- Whether FR-7 is *informational* or a *selector* is unresolved and changes the work materially. A selector means the viewer overrides the automatic language fallback, which touches `pick_file`. Settle this before building.
- "Best possible UX" and "modern selector" are quality bars, not specifications; without reference patterns this phase can expand indefinitely.
- Lazy loading should come last and only if measurement justifies it — most serials will not be long enough to need it.

**Estimated complexity — M.**

---

## Phase 3 · Identity & Access Control

**Goal**
Replace flat, statically configured administrator access with a real role hierarchy and per-administrator permissions.

**Features included**
FR-1 (Super Admin & permissions), FR-2 (role switching), `TASKS.md` P0-4 (`is_banned` enforcement).

**Why this phase comes now**
It is the largest structural item and it **blocks FR-5, FR-4's permission gating, and FR-3**. It comes after Phase 0 because it needs a migration and there must be somewhere to test one.

All three items are deliberately in the same phase because **all three modify the same authorization core**. FR-2's central question — whether Regular User mode merely hides interface or genuinely causes admin actions to be rejected — must be answered while that core is being designed, or the core gets rewritten. P0-4 belongs here for the same reason: a ban check is an authorization check, and adding it later means touching `get_current_user` twice.

**Dependencies**
Phase 0 (test database, and the P0-2 fix so an auth refactor is not entangled with a money bug in the same diff). Three **blocking product decisions** from `FEATURE_REQUESTS.md`: the definitive permission list, how the Super Admin is designated, and whether permissions govern the bot, the panel, or both.

**Files likely to change**
[app/core/admin.py](app/core/admin.py), [app/api/auth.py](app/api/auth.py), [app/api/admin.py](app/api/admin.py), [app/db/models/user.py](app/db/models/user.py), [app/core/config.py](app/core/config.py), [app/bot/middlewares/](app/bot/middlewares/), the four bot admin call sites, [webapp/src/App.tsx](webapp/src/App.tsx), [webapp/src/admin/](webapp/src/admin/), [alembic/versions/](alembic/versions/), [app/locales/](app/locales/)

**Database changes**
**One migration**, covering: a Super Admin member on the role enum (or equivalent), per-administrator permission storage, and administrator identity moving into the database. `chp_users.role` already exists and is **read nowhere**, so it is available. Per `CLAUDE.md` §3, any non-nullable addition needs a `server_default` plus an explicit backfill.

**Backend tasks**
1. Move administrator identity from the `ADMIN_IDS` environment variable into the database, retaining the variable **only** to bootstrap the first Super Admin — otherwise nobody can appoint anybody.
2. Rewrite `is_admin` into a permission-aware check, preserving the single-chokepoint property. Do not scatter checks.
3. Add `require_permission(...)` and a Super-Admin-only dependency.
4. Apply per-permission dependencies across the 41 admin routes, keeping the router-wide admin gate as the outer default so a new route is never accidentally public.
5. Apply permission checks at the four bot call sites.
6. Enforce `is_banned` in bot middleware and `get_current_user`; add an admin endpoint to set it (P0-4 — today nothing can set it).
7. Administrator management endpoints: list, create, remove (guarding the last Super Admin), grant/revoke a single permission.
8. Return role and permissions from `/api/auth/me`.

**Frontend tasks**
1. Replace the 403-probe at [App.tsx:176](webapp/src/App.tsx#L176) with real permission data from `/me`.
2. Administrator management panel: list, create, remove, per-permission toggles.
3. Hide or disable the eight existing tabs per permission.
4. Mode switch control for FR-2.
5. Locale keys for all new strings in all three languages.

**Risks**
- **Highest-risk phase in the plan.** A mistake here is an authorization bypass, not a visual glitch.
- Bootstrap ordering is easy to get wrong: if identity moves to the database without a designated Super Admin, nobody can administer anything. Rehearse on the Phase 0 branch database.
- Two of the five named permissions — Manage Subscriptions and Manage Notifications — currently govern nothing. Decide before building whether to descope them or ship them dormant.
- Manage Users governs a read-only screen; user mutation endpoints do not exist beyond the ban toggle added here.

**Estimated complexity — XL.**

---

## Phase 4 · Commerce Data Model

**Goal**
Turn subscription plans from hardcoded constants into administrator-managed data.

**Features included**
FR-5 (subscription plan management).

**Why this phase comes now**
It **blocks FR-4** — there is nothing to sell until plans exist as data — and it depends on FR-1 for the Manage Subscriptions permission that governs it. Today plans are a two-member enum (`FREE`, `PREMIUM`) with price and duration held in single global environment variables, so changing a price requires a redeploy. That is exactly what the request asks to eliminate.

**Dependencies**
Phase 3 (permission to gate the admin surface), Phase 0 (test database). **Blocking decision:** the meaning of *"add new features"* in FR-5 — adding a benefit line to a plan's advertised list, or defining capabilities the system enforces. The second is an entitlements system and a materially larger design. The data model cannot be settled without this answer.

**Files likely to change**
[app/db/models/user.py](app/db/models/user.py), [app/api/admin.py](app/api/admin.py), [app/services/](app/services/) (new plan service), [app/core/config.py](app/core/config.py), [app/services/payment_review.py](app/services/payment_review.py), [webapp/src/admin/](webapp/src/admin/), [webapp/src/types/admin.ts](webapp/src/types/admin.ts), [alembic/versions/](alembic/versions/)

**Database changes**
**One migration**: a plans table carrying price, duration and benefits, plus data migration of existing `FREE`/`PREMIUM` subscriptions onto plan rows. Existing subscribers must land on an equivalent plan — this is the migration most likely to affect live users, since `chp_subscriptions` currently holds five active rows.

> **Migration-count optimisation.** If FR-5's "add new features" decision lands before Phase 3 begins, the plans table can ship inside Phase 3's migration, reducing the project from four migrations to three. Only do this if the model is genuinely settled; speculative schema is worse than an extra migration.

**Backend tasks**
1. Plan entity with price, duration and benefits; benefits must be translatable per FR-6.
2. Migrate `SubscriptionPlan` usage from enum to foreign key, including in `payment_review.py`.
3. Admin CRUD: create, edit, delete, change price, change duration, edit benefits.
4. Deletion policy for plans with active subscribers — blocked, retained, or migrated. Currently unspecified.
5. Retire `PREMIUM_PRICE` and `PREMIUM_SUBSCRIPTION_DAYS` as the source of truth.

**Frontend tasks**
1. Subscription management tab in the admin panel, gated on Manage Subscriptions.
2. Plan editor form reusing the existing [admin/ui.tsx](webapp/src/admin/ui.tsx) vocabulary rather than new components.
3. Benefits editor.

**Risks**
- The enum-to-table migration touches live subscriptions; a mistake removes paying users' access.
- `payment_review.py` is touched again here, after Phase 0. Sequencing them in the same phase would entangle a money-bug fix with a model refactor, so the separation is deliberate — accept the second visit.
- If "add new features" means enforced entitlements, this phase roughly doubles and should be re-scoped before starting.

**Estimated complexity — L**, or **XL** under the entitlements reading.

---

## Phase 5 · Purchase & Balance Spending

**Goal**
Let users buy a subscription from their balance, and make balance spendable for the first time.

**Features included**
FR-4 (balance display and purchase flow), `TASKS.md` P2-1 (spendable balance).

**Why this phase comes now**
It is the last link in the commerce chain: it needs plans from Phase 4 and the P0-2 fix from Phase 0. Balance is currently credited and **never debited** — `DEDUCTION` and `REFUND` transaction types exist and are referenced nowhere — so this phase opens the first spending path in the system.

**Dependencies**
Phase 4 (plans to sell), Phase 0 (**hard blocker** — introducing spending while approvals can double-credit converts a bookkeeping error into an exploit).

**Files likely to change**
[webapp/src/components/SettingsPage.tsx](webapp/src/components/SettingsPage.tsx), [webapp/src/lib/api.ts](webapp/src/lib/api.ts), new purchase components, [app/api/](app/api/) (new purchase route), [app/services/](app/services/) (purchase service), [app/db/models/user.py](app/db/models/user.py), [app/locales/](app/locales/)

**Database changes**
**None**, if Phase 4's migration includes an idempotency key for purchases — recommended, and the reason this phase carries no migration of its own. The ledger and balance columns already exist.

**Backend tasks**
1. Purchase endpoint: validate the plan, check the balance, debit, activate, and write a `DEDUCTION` ledger row.
2. **The debit and the activation must be atomic.** A debit without activation charges a user for nothing; an activation without a debit gives the plan away. This is the same failure class as P0-2 — apply the same locking discipline.
3. Insufficient-balance response the client can act on.
4. Decide behaviour when a subscription is already active: extend, replace, or refuse. Currently unspecified.
5. Expose the plan catalogue to the Mini App.

**Frontend tasks**
1. Make the balance an interactive control; today it is static text at [SettingsPage.tsx:68](webapp/src/components/SettingsPage.tsx#L68).
2. Plan catalogue screen with price, duration, benefits and a cross-plan comparison.
3. Purchase confirmation.
4. Insufficient-balance dialog with Top Up Balance and Cancel.
5. **A top-up surface in the Mini App.** The Mini App has no payment API at all today; the equivalent flow exists only in the bot. Confirm whether requirement 7 means building this or deep-linking to the bot — the difference is large.
6. Locale keys for all new strings.

**Risks**
- Highest-value, highest-consequence user-facing phase: bugs here take money.
- Requirement 7's "in-app balance top-up page" may be a substantially larger item than it reads, since it implies porting the receipt-upload flow into the Mini App.
- Refunds and cancellation are not addressed anywhere in the request set.

**Estimated complexity — L.**

---

## Phase 6 · Admin Experience Consolidation

**Goal**
Bring the administrative surface up to a consistent, modern standard once its full contents are known.

**Features included**
FR-3 (Super Admin settings page).

**Why this phase comes now**
**Deliberately last among the admin work.** FR-3 asks to *redesign* the Super Admin settings page, but no such page exists — the panel's eight tabs contain no settings surface. Phases 3, 4 and 5 each add administrative interface (administrator list, permission toggles, mode switch, plan editor) that most plausibly belongs on it. Designing it earlier guarantees designing it twice, which directly violates the minimise-UI-rewrites constraint.

**Dependencies**
Phases 3, 4 and 5, for their admin surfaces to exist. **Blocking clarification:** which surface FR-3 refers to — a settings screen inside the panel, the panel as a whole, or the account settings screen as seen by a Super Admin.

**Files likely to change**
[webapp/src/admin/AdminDashboard.tsx](webapp/src/admin/AdminDashboard.tsx), [webapp/src/admin/ui.tsx](webapp/src/admin/ui.tsx), the admin panels added in Phases 3–5, [app/locales/](app/locales/)

**Database changes**
None.

**Backend tasks**
None expected; this is presentational.

**Frontend tasks**
1. Build the settings surface — likely *build*, not *redesign*.
2. Consolidate the components introduced across Phases 3–5 into one coherent layout.
3. Extend the shared vocabulary in `admin/ui.tsx` rather than introducing parallel components.
4. Complete admin-panel localization if Phase 1 deferred it.

**Risks**
- "Modern and professional" is unfalsifiable without reference designs; this phase can absorb unlimited effort. Agree acceptance criteria up front.
- Scope may expand from one page to the whole panel.

**Estimated complexity — M**, entirely dependent on the agreed scope.

---

## Phase 7 · Catalog Localization

**Goal**
Make film titles appear in the viewer's selected language.

**Features included**
FR-6 requirement 3 (per-language movie titles).

**Why this phase comes now**
It is independent of every other phase and can be scheduled freely after Phase 0. It is placed last because it is the least urgent element of FR-6, it needs a migration, and above all it depends on an **unanswered question about who supplies the translations** — which makes it the item most likely to change shape once answered.

**Dependencies**
Phase 0 (migration testing). **Blocking decision:** whether per-language titles are entered by administrators, sourced from an external metadata provider, or machine-translated. This determines whether the phase is a data-entry feature, an integration, or both.

**Files likely to change**
[app/db/models/content.py](app/db/models/content.py), [app/services/content.py](app/services/content.py), [app/services/admin_content.py](app/services/admin_content.py), [app/api/movies.py](app/api/movies.py), [app/services/tmdb.py](app/services/tmdb.py), [webapp/src/admin/TitleEditor.tsx](webapp/src/admin/TitleEditor.tsx), [alembic/versions/](alembic/versions/)

**Database changes**
**One migration.** `Title.name` is currently a single `String(255)` column ([content.py:82](app/db/models/content.py#L82)) with no structure for per-language values. Either a translations table or per-language columns, plus a backfill placing every existing title's current name as its default-language value.

**Backend tasks**
1. Schema for per-language titles, with fallback when a translation is absent.
2. Resolve titles by the viewer's language on every catalog read path.
3. If sourced externally, extend the TMDB client — it can already return localised titles.
4. Admin write path for entering translations.

**Frontend tasks**
1. Per-language title fields in the admin title editor.
2. No viewer-facing change if resolution happens server-side — the preferred approach, since it keeps the client unaware of language resolution.

**Risks**
- Search currently matches on `Title.name`; per-language titles mean deciding whether search covers all languages or only the viewer's.
- 102 titles exist today, so a manual-entry answer implies real data-entry effort per language.
- Genre and collection names are also user-facing catalog text and may fall under the same expectation — confirm scope.

**Estimated complexity — L.**

---

## Recommended Order

**Phase 0** · Stabilise, Unblock, Instrument — *M*
**Phase 1** · Correctness Quick Wins — *S*
**Phase 2** · Playback & Series Experience — *M*
**Phase 3** · Identity & Access Control — *XL*
**Phase 4** · Commerce Data Model — *L*
**Phase 5** · Purchase & Balance Spending — *L*
**Phase 6** · Admin Experience Consolidation — *M*
**Phase 7** · Catalog Localization — *L*

### Notes on the order

**Phases 1 and 2 depend on nothing** and could begin immediately, in parallel with Phase 0's infrastructure work. They deliver the improvements users feel soonest — a translated confirmation message, visible audio languages, and an end to every serial playing episode 1 — at the lowest risk in the plan.

**Phase 0 must precede Phases 3, 4, 5 and 7**, each of which carries a migration.

**Phase 3 is the pivot.** Nothing in Phases 4, 5 or 6 can start until it lands, and it is the single largest and riskiest phase. Its three blocking decisions should be resolved now, in parallel with earlier phases, so it is not waiting on answers when its turn arrives.

**Phase 7 is the one free-floating phase.** It can be slotted anywhere after Phase 0 without disturbing the chain — useful as filler if Phase 3 stalls on decisions.

### Decisions needed before their phase begins

| Decision | Blocks | Needed by |
|---|---|---|
| Definitive permission list | FR-1 data model | Phase 3 |
| How the Super Admin is designated | FR-1 bootstrap | Phase 3 |
| Enforcement scope — bot, panel, or both | FR-1, FR-2 | Phase 3 |
| Whether FR-2's mode switch truly revokes authority | Authorization core | Phase 3 |
| Meaning of "add new features" in FR-5 | Plan data model | Phase 4 |
| Behaviour when purchasing with a subscription active | Purchase flow | Phase 5 |
| Whether the Mini App gets its own top-up flow | Phase 5 scope | Phase 5 |
| Which surface FR-3 refers to | Phase 6 scope | Phase 6 |
| Source of movie title translations | FR-6 req. 3 shape | Phase 7 |
| Reproduction case for FR-8 | FR-8 fix | Phase 1 |
