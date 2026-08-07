# Implementation Status

Assessment of every entry in `FEATURE_REQUESTS.md` against the codebase as it stands.

_Audited: 2026-08-05 · commit `9bd6d48` · updated after Phase 0_

> **Phase 0 complete (2026-08-05).** Every blocker it owned is cleared. The receipt-approval race and two further races in promo redemption are fixed and covered by regression tests that were each verified to fail against the pre-fix code. The production ledger has been cleaned up and the idempotency migration applied; consistency is verified across all 508 users. The project now has 45 passing tests, a throwaway test-database script, a locale parity gate, and CI.
>
> **One item remains open**, and it blocks nothing: P0-5, confirming externally whether a Render Cron Job invokes `app/tasks/cron.py`. Nothing in the repository can answer that.
>
> **Phase 1 complete (2026-08-05).** FR-6 requirements 1–2 and FR-8 are implemented, with 64 tests passing. Two corrections to this document's earlier findings, both discovered while implementing:
>
> - The React admin panel is **entirely** hardcoded Uzbek — ~98 strings across 11 files — not "inconsistently localized" as recorded below. Localizing it moved to Phase 6, where FR-3 rewrites that markup anyway.
> - The `watch` endpoint leaked an internal diagnostic naming a `MediaFile` row directly to the user's screen. That was a mild information disclosure as well as a localization defect, and is fixed.
>
> FR-8 was implemented against the two defects diagnosed in this document, since **no reproduction case was ever supplied**. If the real complaint was something else, it is still open — see Blockers.
>
> **Phase 2 complete (2026-08-07).** FR-7 and FR-9 are implemented, with 85 tests passing. The Mini App now reaches parity with the bot on series navigation: it had no concept of episodes at all, so Watch on a serial always sent episode 1.
>
> Implementing it surfaced a **Phase 0 regression that had already shipped**: removing an "unused" import broke OpenAPI schema generation and therefore `/docs`, while leaving `import app.main` green. Fixed, and now covered by a schema test — `import app.main` cannot catch that class of fault.
>
> **FR-7 was built as an informational display**, matching what the request literally asks for. Letting the viewer *choose* a track is a small follow-on recorded in `IDEAS.md`, not an omission.

---

## Summary

| ID | Feature | Status |
|---|---|---|
| FR-1 | Super Admin & Admin Permission Management | ✅ **Implemented (Phase 3)** |
| FR-2 | Role Switching | ❌ Not Implemented — deferred; see note below |
| FR-3 | Super Admin Settings Page Redesign | ❌ Not Implemented |
| FR-4 | Balance Display & Subscription Purchase | 🟡 Partially Implemented |
| FR-5 | Subscription Plan Management | ❌ Not Implemented |
| FR-6 | Complete Localization Coverage | 🟡 Partially Implemented — **reqs 1–2 done (Phase 1)**; req 3 (per-language titles) remains |
| FR-7 | Audio Languages Before Playback | ✅ **Implemented (Phase 2)** — informational display; selection deliberately out of scope |
| FR-8 | `/start` Command Reliability | ✅ **Implemented (Phase 1)** — against the diagnosed defects, not a supplied repro |
| FR-9 | TV Series Episode & Season Navigation | ✅ **Implemented (Phase 2)** — Mini App reaches parity with the bot |

**Headline:** nothing requested is finished, but little is starting from zero. Six of nine have working foundations — in three cases (FR-6, FR-7, FR-9) the backend already does the hard part and only the presentation layer is absent. The two genuine greenfield items are FR-2 and FR-5, and FR-3 turns out to have no existing surface to redesign.

---

## FR-1 · Super Admin & Admin Permission Management

### ✅ Implemented (Phase 3, 2026-08-07)

| # | Requested behavior | Status |
|---|---|---|
| 1 | Super Admin role distinct from administrator | ✅ `UserRole.SUPER_ADMIN`, exactly one holder |
| 2 | Only Super Admin may create an administrator | ✅ `get_super_admin` gates `POST /admin/admins` |
| 3 | Only Super Admin may remove an administrator | ✅ `DELETE /admin/admins/{id}` |
| 4 | Only Super Admin may assign/revoke permissions | ✅ `PUT /admin/admins/{id}/permissions` |
| 5 | Permissions per administrator, individually | ✅ one row per grant in `chp_admin_permissions` |
| 6 | Every permission independently configurable | ✅ 19 capabilities, each its own toggle |
| 7 | Actions outside granted permissions rejected | ✅ per-route `require_permission` on all 41 routes |
| 8 | Revocation takes effect on subsequent actions | ✅ permissions are read per request, never cached |

**How it is built**

- [app/core/permissions.py](app/core/permissions.py) — the vocabulary. Stored as VARCHAR, so a new capability needs no migration.
- [app/services/permissions.py](app/services/permissions.py) — the **only** place authority is decided, plus administrator management. The REST API reaches it via `require_permission`; the bot via [app/bot/permissions.py](app/bot/permissions.py). `app/core/admin.py` is deleted, so no second path exists.
- Super Admin authority is the role, not 19 grantable rows — otherwise they could revoke their own `manage_admins` and lock the platform out of itself.
- Appointing administrators is Super-Admin-only rather than a `manage_admins` grant: an admin who could grant that could grant themselves everything else.
- Ownership transfers by changing `SUPER_ADMIN_TELEGRAM_ID`; startup promotes the named account and demotes the previous holder to ADMIN.

**Governed surface per permission** — three of the nineteen still govern nothing, unchanged by this phase and tracked elsewhere: `manage_subscriptions` and `manage_subscription_features` await FR-5 (Phase 4), and `manage_notifications` has no notification feature to govern. `manage_users` still governs a read-only screen (see P0-4). They are enforced correctly; they simply gate surfaces that do not exist yet.

**Production** — 1 Super Admin (`6427415448`, 0 explicit rows by design), 1 administrator seeded with all 19, 508 users unchanged.

## FR-2 · Role Switching

### ❌ Not Implemented

**What already exists** — Nothing. No mode concept, no "view as user", no impersonation, no session-level override. Admin status is recomputed from `ADMIN_IDS` on every request.

**What is missing** — The entire feature: a mode toggle, its persistence, and any mechanism for a Super Admin to present as an ordinary user.

**Related files** (where it would land) — [app/api/auth.py](app/api/auth.py), [webapp/src/App.tsx](webapp/src/App.tsx), [webapp/src/components/SettingsPage.tsx](webapp/src/components/SettingsPage.tsx)

**Dependencies** — Hard dependency on **FR-1**: there is no Super Admin role to switch out of. Cannot begin before FR-1 Stage 1.

**Implement first** — Answer the open question of whether Regular User mode only hides admin interface or genuinely causes admin actions to be rejected. The two are very different: hiding is frontend state; genuine revocation must live in the authorization core alongside FR-1 and be verified server-side. Deciding this after FR-1's core is written means rewriting it.

---

## FR-3 · Super Admin Settings Page Redesign

### ❌ Not Implemented

**What already exists** — **No Super Admin settings page exists to redesign.** The admin panel's eight tabs are Stats, Content, Collections, Promo, Receipts, Uploads, Users, Cards ([AdminDashboard.tsx](webapp/src/admin/AdminDashboard.tsx)) — there is no settings tab among them. The only settings screen in the product is the user-facing [SettingsPage.tsx](webapp/src/components/SettingsPage.tsx), which has no admin-specific content.

A shared component vocabulary does exist in [webapp/src/admin/ui.tsx](webapp/src/admin/ui.tsx) (`TextInput`, `Button`, and similar), which any new surface should reuse.

**What is missing** — The page itself, before any question of its visual design.

**Related files** — [webapp/src/admin/AdminDashboard.tsx](webapp/src/admin/AdminDashboard.tsx), [webapp/src/admin/ui.tsx](webapp/src/admin/ui.tsx), [webapp/src/components/SettingsPage.tsx](webapp/src/components/SettingsPage.tsx)

**Dependencies** — Depends on **FR-1** and **FR-2**, both of which introduce admin interface (administrator list, permission toggles, mode switch) that most plausibly belongs on this page.

**Implement first** — Clarify which surface is meant, since the request says "redesign" but nothing matching the description exists. This is likely to become *build* rather than *redesign*. Do it after FR-1 and FR-2, or the same surface gets designed twice.

---

## FR-4 · Balance Display & Subscription Purchase

### 🟡 Partially Implemented

Only requirement 1 of 8 is met.

| # | Requested behavior | Status |
|---|---|---|
| 1 | Balance displayed | ✅ |
| 2 | Tapping balance opens plans | ❌ |
| 3 | Plans show price/duration/benefits/comparison | ❌ |
| 4 | Direct purchase | ❌ |
| 5 | Sufficient balance → deduct + activate | ❌ |
| 6 | Insufficient → dialog with Top Up / Cancel | ❌ |
| 7 | Top Up opens in-app top-up page | ❌ |
| 8 | Cancel dismisses | ❌ |

**What already exists**

- **Balance is displayed** in the Mini App at [SettingsPage.tsx:68](webapp/src/components/SettingsPage.tsx#L68), alongside premium status at [:70](webapp/src/components/SettingsPage.tsx#L70). Also shown in the bot profile ([base.py:121](app/bot/handlers/base.py#L121)).
- **A complete top-up flow exists — in the bot only**: preset amounts ([payment.py:97](app/bot/handlers/payment.py#L97)), card selection, receipt photo upload, admin review.
- **A premium purchase flow exists — in the bot only** ([payment.py:83](app/bot/handlers/payment.py#L83)), charging `settings.PREMIUM_PRICE`.
- Balance crediting and subscription activation are centralised in [payment_review.py](app/services/payment_review.py).

**What is missing**

- **Balance is credited but never debited.** `BalanceTxType.DEDUCTION` and `REFUND` ([user.py:46](app/db/models/user.py#L46)) exist and are referenced nowhere. There is no spending path in the system, which is the core of requirement 5.
- **No plan catalogue.** Nothing to open when the balance is tapped — see FR-5.
- **The Mini App has no payment surface at all.** [webapp/src/lib/api.ts](webapp/src/lib/api.ts) contains no top-up, purchase, or subscription method outside the admin section. Requirement 7's "in-app balance top-up page" does not exist; today the equivalent lives in the bot.
- The balance is static text, not an interactive control.
- No insufficient-balance dialog.

**Related files** — [webapp/src/components/SettingsPage.tsx](webapp/src/components/SettingsPage.tsx), [webapp/src/lib/api.ts](webapp/src/lib/api.ts), [app/bot/handlers/payment.py](app/bot/handlers/payment.py), [app/services/payment_review.py](app/services/payment_review.py), [app/db/models/user.py](app/db/models/user.py)

**Dependencies** — Hard dependency on **FR-5**: there is nothing to sell without plan data. Overlaps `TASKS.md` **P2-1**. ⚠️ **Blocked by P0-2**, the confirmed receipt-approval race: introducing a spending path while approvals can double-credit turns a bookkeeping error into an exploit.

**Implement first** — Fix P0-2, then build FR-5's plan model. Only then the purchase transaction — which must make the debit and the activation atomic, since a debit without activation charges for nothing and an activation without debit gives the plan away.

---

## FR-5 · Subscription Plan Management

### ❌ Not Implemented

**What already exists** — Not a plan system, but the pieces it would replace:

- `SubscriptionPlan` is a **hardcoded two-member enum** — `FREE`, `PREMIUM` ([user.py:41](app/db/models/user.py#L41)). Plans are code, not data.
- Price and duration are **single global environment variables**: `PREMIUM_PRICE` and `PREMIUM_SUBSCRIPTION_DAYS` ([config.py:56-57](app/core/config.py#L56-L57)). One price, one duration, platform-wide.
- `chp_subscriptions` records a user's plan and expiry ([user.py:102](app/db/models/user.py#L102)).

**What is missing** — All seven requested operations, and the data model they require:

- **No plan entity.** Plans cannot be created, edited, or deleted because they are not rows.
- **No benefits field anywhere** — no storage for the benefit text FR-4 must display.
- **No admin subscription endpoints.** Of 41 admin routes, none manages plans or subscriptions.
- **No admin UI** — no subscription tab in the panel.
- Changing a price today means editing an environment variable and redeploying, which is precisely what the request asks to eliminate.

**Related files** — [app/db/models/user.py](app/db/models/user.py), [app/core/config.py](app/core/config.py), [app/api/admin.py](app/api/admin.py), [webapp/src/admin/AdminDashboard.tsx](webapp/src/admin/AdminDashboard.tsx)

**Dependencies** — Blocks **FR-4** entirely. Governed by **FR-1**'s Manage Subscriptions permission, which currently has no surface to govern. Plan benefits are user-facing text, so they fall under **FR-6**. Requires a migration → **P0-3**.

**Implement first** — Resolve the *"add new features"* ambiguity recorded in `FEATURE_REQUESTS.md`. If it means adding benefit lines to a plan's advertised list, this is straightforward CRUD. If it means defining capabilities the system then enforces, it is a substantially larger entitlements system. The data model cannot be designed without that answer. Then convert `SubscriptionPlan` from enum to table — the migration must map existing `FREE`/`PREMIUM` rows onto the new plans.

---

## FR-6 · Complete Localization Coverage

### 🟡 Partially Implemented

Strong foundation for interface text; the cited defect is real; catalog translation is absent.

**What already exists**

- **A mature single-catalog i18n system.** `app/locales/{uz,ru,en}.json` — 186 keys each, verified identical across all three. The bot reads them via [app/core/i18n.py](app/core/i18n.py); the Mini App fetches the same files from [app/api/i18n.py](app/api/i18n.py). One change reaches both surfaces.
- Uzbek fallback merged server-side, so a key present in any locale resolves.
- `chp_users.language` is shared between bot and Mini App, so a language change applies to both.
- The viewer-facing Mini App is fully localized — all 36 keys it uses resolve in all three languages.

**What is missing**

- ⚠️ **The cited defect is confirmed and located.** [app/api/movies.py:263](app/api/movies.py#L263) returns a hardcoded English string:
  `message="Check your chat with the bot — the video is on its way."`
  It is built into the API response, so it reaches every user in English regardless of their language.
- **Two further hardcoded strings** found in the same sweep: [UploadsPanel.tsx:219](webapp/src/admin/UploadsPanel.tsx#L219) hardcodes Uzbek (`"Yangi yuklama yo'q. Botga video yuboring."`), and [ThemeToggle.tsx:12](webapp/src/components/ThemeToggle.tsx#L12) hardcodes English aria-labels.
- The admin panel is **inconsistently localized** — some translated strings, some hardcoded.
- **No per-language movie titles.** [content.py:82](app/db/models/content.py#L82) defines `Title.name` as a **single** `String(255)` column. There is no structure for a title per language, and no fallback logic. Requirement 3 needs a schema change, not a translation pass.

**Related files** — [app/api/movies.py](app/api/movies.py), [app/locales/](app/locales/), [app/core/i18n.py](app/core/i18n.py), [app/db/models/content.py](app/db/models/content.py), [webapp/src/admin/UploadsPanel.tsx](webapp/src/admin/UploadsPanel.tsx), [webapp/src/components/ThemeToggle.tsx](webapp/src/components/ThemeToggle.tsx)

**Dependencies** — Governs user-facing text in FR-4 (dialog copy), FR-5 (plan benefits) and FR-7 (audio labels), so its conventions should be settled before those ship their strings. Requirement 3 requires a migration → **P0-3**. Relates to `TASKS.md` **P1-3** (automating the locale parity check, which would have caught these).

**Implement first** — Split this item in two. Requirements 1–2 are a small, immediate fix: move `movies.py:263` into the catalogs and sweep the remaining hardcoded strings; automating P1-3 first makes the sweep exhaustive rather than best-effort. Requirement 3 is a separate project needing a schema change and an answer on who supplies the translations. Do not let the second block the first.

---

## FR-7 · Audio Languages Before Playback

### 🟡 Partially Implemented

The backend is finished. The UI does not exist.

**What already exists**

- `AudioLanguage` models five values — `uz_dub`, `uz_sub`, `ru`, `en`, `original` ([content.py:55](app/db/models/content.py#L55)) — and every `MediaFile` carries one ([content.py:160](app/db/models/content.py#L160)).
- ⭐ **`content_service.available_languages(session, episode_id)` already exists** and returns exactly what this feature needs — [content.py:421](app/services/content.py#L421). **It is called from nowhere.** A repository-wide search returns only its definition. The data layer for FR-7 was written and never surfaced.
- Language labels are already translated: `audio.uz_dub`, `audio.ru`, and so on exist in all three locales.
- Playback already picks a file by language preference via `pick_file` with a fallback chain.

**What is missing**

- **No REST endpoint exposes it.** `available_languages` has no route, so the Mini App cannot reach it.
- **No UI anywhere** shows a title's available audio languages before playback — not in [MovieDetailSheet.tsx](webapp/src/components/MovieDetailSheet.tsx), not in the bot's title view.
- No language *choice* at playback: `pick_file` decides automatically from the user's UI language; the viewer cannot override it.
- Note the existing [AudioFilter.tsx](webapp/src/components/AudioFilter.tsx) is a **catalog-wide filter**, not a per-title display. It answers "show me titles with Russian", not "what does *this* film offer" — related but not this feature.

**Related files** — [app/services/content.py:421](app/services/content.py#L421), [app/api/movies.py](app/api/movies.py), [webapp/src/components/MovieDetailSheet.tsx](webapp/src/components/MovieDetailSheet.tsx), [app/locales/](app/locales/)

**Dependencies** — Effectively none; the lightest item in the list. Shares a surface with **FR-9** (both concern pre-playback presentation) and shares label text with **FR-6**. If FR-7's display is to be *interactive* rather than informational, it couples to FR-9's episode selector, since audio availability is per episode.

**Implement first** — Expose `available_languages` through a public endpoint, then render it in the detail sheet. **This is the cheapest win in the entire backlog** — the service method, the enum, and the translated labels all exist; only a route and a component are missing. Confirm first whether the display is informational or a selector, since a selector is materially more work.

---

## FR-8 · `/start` Command Reliability

### 🟡 Partially Implemented

**What already exists** — [base.py:48-63](app/bot/handlers/base.py#L48-L63) already distinguishes the two cases the request names:

- It checks whether the user exists **before** get-or-create ([:53-54](app/bot/handlers/base.py#L53)), so `is_new_user` is accurate.
- New users are shown the language picker first, because the welcome text itself needs a language ([:58-61](app/bot/handlers/base.py#L58)).
- Existing users are sent the main menu in their stored language ([:63](app/bot/handlers/base.py#L63)).
- Referral payloads from deep links are captured via `command.args`.

**What is missing** — The request implies a defect, but the failing case has not been described, so this cannot be confirmed fixed or reproduced. Two observable gaps, offered as **candidates, not confirmed diagnoses**:

1. **Abandoned language selection.** A user who sends `/start`, receives the language picker, and never taps a button has a row in the database. On their next `/start` they count as existing and go straight to the main menu — never having chosen a language, with `language_selected` still false. They are never asked again by the bot.
2. **No ban check.** `/start` does not consult `is_banned`; nothing in the system does (`TASKS.md` **P0-4**). If "verify" means checking whether a user is permitted, that check does not exist anywhere.

**Related files** — [app/bot/handlers/base.py](app/bot/handlers/base.py), [app/services/users.py](app/services/users.py), [app/db/models/user.py](app/db/models/user.py)

**Dependencies** — If "verify" includes ban enforcement, this merges with **P0-4**. Otherwise independent.

**Implement first** — Obtain the failing case. This is the only item in the backlog where the *problem* is undefined rather than the solution; building against a guess risks fixing something that was never broken. If no repro is available, candidate 1 above is the most likely reading of "existing users should always receive the correct interface".

---

## FR-9 · TV Series Episode & Season Navigation

### 🟡 Partially Implemented

Strong in the bot, entirely absent in the Mini App.

| # | Requested behavior | Bot | Mini App |
|---|---|---|---|
| 1 | Open any episode directly | 🟡 Via paginated list | ❌ |
| 2 | Modern episode selector | 🟡 Inline keyboard | ❌ |
| 3 | Season support | ✅ | ❌ |
| 4 | Lazy loading / infinite scroll | 🟡 Paginated | ❌ |
| 5 | Best-possible season navigation UX | 🟡 Functional | ❌ |

**What already exists**

- **A correct data model.** `Title → Episode → MediaFile`, with `season` and `number` on each episode and a uniqueness constraint per title ([content.py:126](app/db/models/content.py#L126)).
- **Service layer complete** — `list_episodes(title_id, season)` and `list_seasons(title_id)` ([content.py:227](app/services/content.py#L227), [:234](app/services/content.py#L234)).
- **The bot has working season and episode navigation**: season selection ([catalog.py:367](app/bot/handlers/catalog.py#L367)) and episode pagination ([catalog.py:376](app/bot/handlers/catalog.py#L376)).

**What is missing**

- ⚠️ **The Mini App cannot select an episode at all.** [movies.py:243](app/api/movies.py#L243) states it plainly: *"The Mini App lists titles, not episodes — deliver the first one."* Pressing Watch on a serial **always sends episode 1**, whatever the user has already seen. For a serial of any length this is the request's central complaint.
- **No public episode endpoints.** `list_episodes` and `list_seasons` exist in the service layer but are exposed only through **admin** routes ([api.ts:127](webapp/src/lib/api.ts#L127)). The viewer-facing `/api/movies` router has none.
- No episode or season UI in the Mini App — [MovieDetailSheet.tsx](webapp/src/components/MovieDetailSheet.tsx) offers a single Watch button.
- No lazy loading or infinite scroll anywhere; the bot paginates with inline buttons instead.
- No watched/unwatched indication in either surface, though `chp_watch_history` holds the data.

**Related files** — [app/api/movies.py:243](app/api/movies.py#L243), [app/services/content.py](app/services/content.py), [app/bot/handlers/catalog.py](app/bot/handlers/catalog.py), [webapp/src/components/MovieDetailSheet.tsx](webapp/src/components/MovieDetailSheet.tsx), [webapp/src/lib/api.ts](webapp/src/lib/api.ts)

**Dependencies** — Shares the pre-playback surface with **FR-7**; if audio language is selectable it becomes per-episode, coupling the two. Otherwise independent of the admin and monetization work, so it can proceed in parallel.

**Implement first** — Expose `list_seasons` and `list_episodes` as public endpoints, then change `POST /movies/{id}/watch` to accept an episode id instead of silently choosing the first. That single change removes the worst behavior — always playing episode 1 — before any selector UI is designed. Build the selector after; lazy loading last, since it only matters past a list length worth measuring first.

---

## Cross-Feature Sequencing

**Three prerequisites sit outside this backlog** and gate parts of it:

| Prerequisite | Status | Blocks | Why |
|---|---|---|---|
| **P0-2** — receipt approval race | ✅ **Resolved** — fixed, tested, production ledger cleaned, backstop index applied | FR-4 | A spending path plus double-crediting approvals is an exploit, not a bug |
| **P0-3** — no non-production database | ✅ **Resolved for testing** via `scripts/test_db.sh`; a Neon branch is still worth having for migration rehearsal against production-shaped data | FR-1, FR-5, FR-6 (req. 3) | All three need migrations. 20 database-backed tests now run against a real PostgreSQL |
| Decisions in `FEATURE_REQUESTS.md` | ⛔ Still open | FR-1, FR-2, FR-5 | Permission list, Super Admin designation, and "add new features" all gate data models |

**Recommended order**, dependency-first:

1. **FR-7** — cheapest win; the service method and translations already exist, only a route and a component are missing.
2. **FR-6 requirements 1–2** — small, fixes a confirmed live defect at [movies.py:263](app/api/movies.py#L263).
3. **FR-9 backend** — public episode endpoints and an episode-aware watch call; ends "always plays episode 1".
4. **FR-8** — once a repro is supplied; may fold into P0-4.
5. **P0-2 fix**, then **P0-3** — clears the blockers below.
6. **FR-1** — the largest structural item; unblocks FR-2, FR-3, FR-5.
7. **FR-5** — plan model, after FR-1's permission for it exists.
8. **FR-4** — the purchase flow, once plans exist and P0-2 is fixed.
9. **FR-2** — after FR-1's authorization core is settled.
10. **FR-3** — last, so the surface is designed once.
11. **FR-6 requirement 3** and **FR-9 UI polish** — independent, schedulable whenever.

Items 1–4 are independent of every blocker and can start immediately.
