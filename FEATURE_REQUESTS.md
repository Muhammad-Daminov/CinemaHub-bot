# Feature Requests

Single source of truth for all product requirements.
This document records **what has been requested**. It does not assess feasibility, existing implementation status, or effort — implementation status lives in `IMPLEMENTATION_STATUS.md`, and scheduled work lives in `TASKS.md`.

**Maintenance rules:** new requests are appended here, never into a second document. Overlapping requests are merged into the existing entry with both phrasings preserved. Priorities are re-cut whenever new requests arrive. Nothing is ever deleted.

**Feature IDs** (`FR-n`) follow the requester's original numbering and are stable. Display order within this document follows priority and grouping, not numeric order.

---

## Intake Log

| ID | Title | Intake |
|---|---|---|
| FR-1 | Super Admin & Admin Management | ✅ Complete — received twice (2026-08-05, both batches); merged |
| FR-2 | Role Switching | ✅ Complete |
| FR-3 | Super Admin Settings | ✅ Complete |
| FR-4 | User Balance & Subscription System | ✅ Complete |
| FR-5 | Subscription Management | ✅ Complete |
| FR-6 | Localization | ✅ Complete |
| FR-7 | Audio Languages | ✅ Complete |
| FR-8 | `/start` Command | ✅ Complete |
| FR-9 | TV Series Experience | ✅ Complete |

_History: an earlier batch delivered FR-1 in full with FR-2 and FR-9 as titles only, and FR-3 through FR-8 absent. All nine are now specified. Batch 1 of an ongoing series — further requests expected._

**Priority tiers are provisional.** No batch has carried explicit priority labels; the requester's numbering is a list order, not a ranking. Every placement is flagged for confirmation in its TODO section.

---

## High Priority
*(Core features required before release)*

### Group A · Administration & Access Control

---

#### FR-1 · Super Admin Role & Granular Admin Permission Management

**Description**

Introduce a dedicated **Super Admin** role that sits above the existing administrator level, together with a permission system controlling what each individual administrator may do.

Administrator authority is currently uniform — every administrator holds the same capabilities. This request replaces that flat model with a two-tier hierarchy plus per-administrator, per-capability permissions, modelled on the administrator permission system used by Telegram groups and channels, where each administrator is granted a specific subset of rights rather than blanket access.

The Super Admin is the sole role permitted to create administrators, remove administrators, and modify any administrator's permissions.

**User Story**

> As the Super Admin, I want to appoint administrators and grant each of them only the specific permissions their responsibilities require, so that I can delegate day-to-day operational work without giving every administrator full control over the entire platform.

**Expected Behavior**

1. A **Super Admin** role exists and is distinct from the standard administrator role.
2. Only the Super Admin may **create** a new administrator.
3. Only the Super Admin may **remove** an existing administrator.
4. Only the Super Admin may **assign or revoke** permissions on an administrator.
5. Permissions are managed **per administrator, individually** — two administrators may hold entirely different permission sets.
6. Every permission is **independently configurable**: each may be granted or revoked on its own, without affecting any other permission.
7. An administrator may perform an action **only** where the corresponding permission has been granted. Actions outside an administrator's granted permissions are rejected.
8. Revoking a permission takes effect for that administrator's subsequent actions.

**Admin Requirements**

An administration interface allowing the Super Admin to:

- View all current administrators.
- Create a new administrator.
- Remove an existing administrator.
- View the permissions held by any individual administrator.
- Grant or revoke each permission individually for a selected administrator, each presented as its own independently togglable control.

**Permission Categories**

Named explicitly across both request batches:

| Permission | Governs |
|---|---|
| Manage Users | User account administration |
| Manage Movies | Film and content catalog administration |
| Manage Subscriptions | Subscription administration — see **FR-5** |
| Manage Payments | Payment administration |
| Manage Notifications | Notification administration |

Both batches listed these followed by "etc.", indicating the set is **open-ended and not exhaustive**.

**Technical Notes**

- The permission model is explicitly specified as analogous to Telegram's group and channel administrator permission system: a per-administrator set of individually togglable rights.
- "Every permission should be configurable" implies permissions must be discrete, independently addressable values rather than fixed named roles or preset bundles.
- Enforcement is required at the point an action is performed, not merely hidden in the interface — Expected Behavior 7 states actions outside granted permissions are rejected.
- **Merge record.** Requested in two batches with equivalent meaning. Batch 1: *"There should be a Super Admin role… Only the Super Admin can create or remove admins… assign or revoke permissions for each admin individually, just like Telegram's group/channel admin permission system… (manage users, manage subscriptions, manage movies, manage payments, manage notifications, etc.)"*. Batch 2 restated the same four points with the permission list reordered to *"Users, Movies, Subscriptions, Payments, Notifications, etc."*. No requirement differs between them.
- The **Manage Subscriptions** permission governs the capability specified in **FR-5**; the two must be designed together.

**TODO**

1. **Definitive permission list.** Both batches end the list with "etc." The complete, closed set must be defined.
2. **Super Admin designation.** How the Super Admin is established is not stated — by configuration, database flag, promotion of an existing account, or otherwise.
3. **Number of Super Admins.** Whether exactly one exists, or several may hold the role simultaneously.
4. **Super Admin's own permissions.** Whether the Super Admin implicitly holds all permissions, and whether any of their own rights can be revoked.
5. **Scope of enforcement.** Whether permissions govern the administrative web interface, administrative bot commands, or both.
6. **Default permissions on creation.** What a newly created administrator holds before configuration — none, or some default set.
7. **"Manage Notifications" scope.** Implies notification functionality not otherwise described in any batch. Confirm what it governs.
8. **Priority placement.** Provisionally High, on the basis that batch 1 opened by describing the admin system as incomplete. Confirm.

---

### Group B · Monetization

---

#### FR-4 · User Balance Display & In-App Subscription Purchase

**Description**

Surface the user's account balance in the interface and make it the entry point to purchasing a subscription. Tapping the balance opens the subscription plan catalogue, from which a user can buy a plan directly, paying from their balance. Where the balance is insufficient, the user is offered an immediate route to topping up rather than being left at a dead end.

**User Story**

> As a user, I want to see my balance and buy a subscription plan directly from it, so that I can upgrade in a few taps without leaving the app or working out how to pay.

**Expected Behavior**

1. The user's **balance is displayed** in the interface.
2. **Tapping the balance opens the subscription plans** screen.
3. Each plan displays:
   - **Price**
   - **Duration**
   - **Benefits**
   - **Feature comparison** across plans
4. The user can **purchase a plan directly** from this screen.
5. **When the balance is sufficient:**
   - The plan price is deducted from the balance.
   - The subscription is activated.
6. **When the balance is insufficient**, a dialog is shown containing:
   - The message *"Your balance is insufficient."*
   - A **Top Up Balance** action
   - A **Cancel** action
7. Choosing **Top Up Balance** automatically opens the in-app balance top-up page.
8. Choosing **Cancel** dismisses the dialog without change.

**Admin Requirements**

The plans presented here are those defined and maintained under **FR-5**. No separate administration surface is requested by this item.

**Technical Notes**

- This item defines the **purchase** path; **FR-5** defines the **management** path for the plans it displays. Neither is complete without the other.
- All user-facing strings in this flow — including the insufficient-balance dialog and its two actions — fall under **FR-6** and must be available in every supported language.
- Requirement 5 couples a balance deduction to a subscription activation. These must not be able to diverge, since a deduction without activation charges a user for nothing and an activation without deduction gives the plan away.

**TODO**

1. **Concurrent or existing subscription.** Behavior when a user purchases while a subscription is already active is unspecified — extend the existing term, replace it, or refuse the purchase.
2. **"Feature comparison" presentation.** A comparison across plans is required but its format is unspecified — side-by-side table, per-plan list, or otherwise.
3. **Dialog copy.** Whether *"Your balance is insufficient."* is final wording or placeholder copy, and how it is phrased in each language.
4. **Top-up page identity.** Whether "the in-app balance top-up page" refers to an existing top-up surface or a new screen to be designed under this item.
5. **Return path after top-up.** Whether the user returns to the pending purchase after topping up, or is left on the top-up page.
6. **Partial balance.** Whether a user may combine part of their balance with another payment method, or must hold the full price.
7. **Refunds and cancellation.** Not addressed by this request.
8. **Surface.** Whether this flow is required in the Mini App, the bot, or both.
9. **Priority placement.** Provisionally High as core monetization. Confirm.

---

#### FR-5 · Subscription Plan Management

**Description**

Give the Super Admin full lifecycle control over subscription plans from the admin panel. Every attribute of a plan — its price, its duration, and the benefits it advertises — must be editable without a code change, and new plans must be creatable on demand.

**User Story**

> As the Super Admin, I want to create and adjust subscription plans, their pricing, duration, and benefits from the admin panel, so that I can change commercial terms without a developer or a deployment.

**Expected Behavior**

The Super Admin can:

1. **Create** a subscription plan.
2. **Edit** an existing plan.
3. **Delete** a plan.
4. **Change the price** of a plan.
5. **Change the duration** of a plan.
6. **Edit the benefits** listed for a plan.
7. **Add new features** to a plan.

Everything related to subscriptions must be manageable from the admin panel.

**Admin Requirements**

This item is entirely admin-facing. The admin panel must provide a subscription management surface exposing all seven operations above. Access is governed by the **Manage Subscriptions** permission defined in **FR-1**.

**Technical Notes**

- The plans managed here are those displayed to users under **FR-4**. Price, duration, and benefits are the same fields in both items and must share one definition.
- Requirement: *"Everything related to subscriptions must be manageable from the admin panel"* — the seven listed operations are explicitly a floor, not a ceiling.
- Plan benefits are user-facing text and therefore fall under **FR-6**.

**TODO**

1. **"Add new features" meaning.** Ambiguous and consequential. This may mean (a) adding a benefit line item to a plan's advertised list, or (b) defining new platform capabilities that a plan grants and the system then enforces. Interpretation (b) is substantially larger work. Clarify before design.
2. **Deleting a plan with active subscribers.** Unspecified — whether deletion is blocked, subscribers are retained on the deleted terms, or subscribers are migrated.
3. **Price changes and existing subscribers.** Whether a change applies at next renewal, immediately, or only to new purchases.
4. **Benefits representation.** Whether benefits are free-form text or structured items, and how they are translated per **FR-6**.
5. **Priority placement.** Provisionally High, since **FR-4** cannot function without plans to sell. Confirm.

---

### Group C · Platform Correctness

---

#### FR-6 · Complete Localization Coverage

**Description**

Every piece of text the user sees must respect their selected language. This covers interface strings, system and bot messages, and — as an explicit extension beyond interface text — **movie titles**, which should also change according to the selected language.

The requester cites a concrete failure: the message *"Check your chat with the bot…"* is not translated.

**User Story**

> As a user who has selected a language, I want every text I encounter — including movie titles — to appear in that language, so that the app is consistently usable and no part of it falls back to a language I did not choose.

**Expected Behavior**

1. **All text respects the user's selected language**, with no exceptions.
2. Messages of the kind cited — *"Check your chat with the bot…"* — are translated.
3. **Movie titles change according to the selected language.**

**Admin Requirements**

Not specified by this request. Note that if per-language movie titles are supplied manually, an administrative surface for entering them would be implied — see TODO 1.

**Technical Notes**

- Requirements 1 and 2 concern interface and system strings. Requirement 3 concerns **catalog data**, which is a different class of problem: it implies storing or sourcing a title per language rather than translating a fixed string set.
- This item governs user-facing text introduced by every other feature in this document — notably the **FR-4** insufficient-balance dialog, **FR-5** plan benefits, and **FR-7** audio language labels.

**TODO**

1. **Source of movie title translations.** Whether per-language titles are entered manually by administrators, sourced from an external metadata provider, or machine-translated. This determines whether FR-6 is a data-entry feature, an integration, or both.
2. **Fallback for missing title translations.** What is displayed when a title has no translation in the selected language.
3. **Scope of the audit.** Whether *"Check your chat with the bot…"* is the only known untranslated string or an example, with a full sweep for untranslated text expected.
4. **Language set.** Whether requirement 3 applies to all supported languages equally.
5. **Priority placement.** Provisionally High — requirement 1 is stated as absolute and the request cites a live defect. Confirm.

---

#### FR-8 · `/start` Command Reliability

**Description**

The `/start` command must behave correctly for both new and existing users. An existing user issuing `/start` should always be presented with the correct interface for their account state.

**User Story**

> As an existing user, I want `/start` to return me to the correct interface every time, so that restarting the bot never leaves me in a broken or unexpected state.

**Expected Behavior**

1. `/start` **verifies both new and existing users**.
2. An **existing user** issuing `/start` **always receives the correct interface**.

**Admin Requirements**

None specified.

**Technical Notes**

- The phrasing implies a current defect in which existing users do not reliably receive the correct interface. The failing case has not been described.

**TODO**

1. **Meaning of "verify".** Unspecified — whether this refers to confirming the user record exists, checking subscription state, checking ban state, confirming identity, or something else.
2. **Definition of "the correct interface".** What an existing user should see, and how that differs from what they currently see.
3. **Reproduction.** The circumstances under which an existing user currently receives an incorrect interface, which is needed to confirm a fix.
4. **Priority placement.** Provisionally High as a correctness defect on the primary entry point. Confirm.

---

## Medium Priority
*(Important improvements)*

### Group D · Content Experience

---

#### FR-9 · TV Series Episode & Season Navigation

**Description**

Rework the serial-viewing experience so that any episode can be opened directly through a modern episode selector with full season support. Large episode lists must load progressively rather than all at once, and season navigation should follow the strongest available UX pattern.

**User Story**

> As a viewer of a serial, I want to jump straight to any episode and move between seasons easily, so that I can find the episode I want without scrolling through a long undifferentiated list.

**Expected Behavior**

1. Users can **open any episode directly**.
2. Episode selection uses a **modern episode selector**.
3. **Seasons are supported.**
4. Large episode lists use **lazy loading or infinite scrolling**.
5. Season navigation is presented with **the best possible UX**.

**Admin Requirements**

None specified.

**Technical Notes**

- Requirement 4 is a performance requirement tied to list length; requirements 2 and 5 are presentation requirements.
- Requirement 5 is a quality bar rather than a specification — see TODO 2.

**TODO**

1. **Meaning of "open any episode directly".** Whether this means navigation within the app, or an external deep link that opens a specific episode.
2. **"Best possible UX" definition.** Subjective as written. Reference patterns or a preferred layout are needed before design.
3. **Lazy-loading threshold.** The episode count at which progressive loading engages, and page size.
4. **Watched state.** Whether the selector should indicate which episodes the user has already watched.
5. **Surface.** Whether this applies to the Mini App, the bot, or both.
6. **Priority placement.** Provisionally Medium — a substantial UX improvement, not stated as release-blocking. Confirm.

---

#### FR-7 · Audio Language Visibility Before Playback

**Description**

Display the audio languages available for a film clearly and attractively before playback begins, so the viewer knows what they will hear before committing to watch.

**User Story**

> As a viewer, I want to see which audio languages a film is available in before I start it, so that I do not begin watching something I cannot understand.

**Expected Behavior**

1. **Available audio languages are shown for every movie.**
2. They are presented **clearly and attractively**.
3. They are shown **before playback**.

**Admin Requirements**

None specified.

**Technical Notes**

- Audio language labels are user-facing text and fall under **FR-6**.

**TODO**

1. **Placement.** Whether languages appear on the film detail screen, at the moment playback is requested, or both.
2. **Interactivity.** Whether the user selects an audio language at this point, or the display is informational only.
3. **Presentation.** "Clear and attractive" is subjective; a preferred visual treatment is needed.
4. **Unavailable languages.** Whether languages the film lacks should be shown as unavailable or omitted entirely.
5. **Priority placement.** Provisionally Medium. Confirm.

---

### Group E · Administration (continued)

---

#### FR-2 · Super Admin / Regular User Mode Switching

**Description**

Allow the Super Admin to switch between Super Admin mode and Regular User mode with a single click, for testing purposes — so the platform can be experienced exactly as an ordinary user sees it without a second account.

**User Story**

> As the Super Admin, I want to switch to Regular User mode in one click, so that I can test the ordinary user experience without maintaining a separate test account.

**Expected Behavior**

1. The Super Admin can switch between **Super Admin mode** and **Regular User mode**.
2. Switching takes **one click**.
3. The stated purpose is **testing**.

**Admin Requirements**

A single, readily accessible control for the Super Admin to toggle modes.

**Technical Notes**

- Depends on the Super Admin role established in **FR-1**; it cannot exist before that role does.
- Whether the switch is cosmetic or genuinely revokes authority is the central design question — see TODO 1.

**TODO**

1. **Depth of the switch.** Whether Regular User mode only hides administrative interface, or actually causes administrative actions to be rejected while active. These are materially different in effort and in safety.
2. **Returning to Super Admin mode.** How the switch back is performed once administrative interface is hidden.
3. **Persistence.** Whether the mode survives reload and across sessions, or resets.
4. **Eligibility.** Whether this is Super Admin only, or available to all administrators.
5. **Surface.** Whether mode switching applies to the Mini App, the bot, or both.
6. **Priority placement.** Provisionally Medium — a testing convenience rather than a user-facing capability, though useful while **FR-1** is being validated. Confirm.

---

## Low Priority
*(Nice-to-have improvements)*

---

#### FR-3 · Super Admin Settings Page Redesign

**Description**

Redesign the Super Admin settings page with a modern, professional user interface.

**User Story**

> As the Super Admin, I want the settings page to look modern and professional, so that administering the platform feels consistent with the quality of the product.

**Expected Behavior**

1. The Super Admin settings page is **redesigned**.
2. The result is **modern and professional** in appearance.

**Admin Requirements**

This item is entirely admin-facing; it concerns the presentation of an administrative surface rather than any new capability.

**Technical Notes**

- This is a presentation request. No functional change, new data, or new capability is described.
- It will be affected by **FR-1**, which introduces administrator management interface, and by **FR-2**, which introduces a mode-switching control — both of which may need to live on or near this page. Sequencing the redesign after those items would avoid redesigning the same surface twice.

**TODO**

1. **Which surface.** "The Super Admin settings page" is not identified — whether it means a settings screen within the admin panel, the admin panel as a whole, or the account settings screen as seen by a Super Admin.
2. **Definition of "modern and professional".** Subjective as written. Reference designs, or specific complaints about the current appearance, are needed before work can begin.
3. **Scope boundary.** Whether the redesign covers only this page or extends to the wider administrative interface.
4. **Priority placement.** Provisionally Low — purely presentational, with no described functional impact. Confirm; the requester may consider it more urgent.

---

## Cross-Cutting Relationships

Dependencies and overlaps between items, recorded so they are not rediscovered later:

| Relationship | Detail |
|---|---|
| **FR-1 ↔ FR-5** | FR-1's *Manage Subscriptions* permission governs the capability FR-5 specifies. Design together. |
| **FR-4 ↔ FR-5** | FR-4 sells the plans FR-5 defines. Price, duration, and benefits are the same fields; FR-4 is non-functional without FR-5. |
| **FR-6 → FR-4, FR-5, FR-7** | FR-6 governs all user-facing text, including FR-4's dialog copy, FR-5's plan benefits, and FR-7's audio language labels. |
| **FR-1 → FR-2** | Mode switching requires the Super Admin role to exist first. |
| **FR-1, FR-2 → FR-3** | Both add administrative interface that may belong on the page FR-3 redesigns. Redesigning first risks doing it twice. |
| **FR-7 ↔ FR-9** | Both concern pre-playback presentation of a title and may share a surface. |
