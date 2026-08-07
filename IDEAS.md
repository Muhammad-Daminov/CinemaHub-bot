# IDEAS.md

Idea pool — raised in conversation or left as a note in the code, **not yet built**.

Nothing here is committed work. When an idea is accepted it moves to `TASKS.md` and is marked `→ Promoted` below (kept, not deleted). Rejected ideas keep their reason so they are not re-proposed.

**Deduplication rule:** an idea lives in exactly one place. If it is in `TASKS.md`, this file only carries a one-line pointer.

---

## Open — not yet prioritized

### I-1 · Premium value proposition
Premium currently unlocks unlimited AI recommendations and nothing else, at 50,000 per 30 days. Candidate benefits raised:
- ~~Exemption from the 15-minute auto-delete window~~ — **no longer possible**: auto-deletion was removed entirely in Phase 3, so every viewer already keeps their videos
- Higher-quality files (`VideoQuality` already models this, unused as a gate)
- Early access to new titles
- Larger or unlimited favorites
- Skip the manual-receipt wait — instant activation from balance

**Open question:** which of these are cheap to build vs. actually persuasive to this audience? Needs a product decision, not an engineering one. → tracked as **P2-2**.

### I-2 · Referral reward design
Mechanism exists, payout does not. Designs considered:
- Credit both parties when the referee's **first payment is approved** — resists self-referral farming better than rewarding on signup
- Grant premium days instead of balance — costs nothing marginal, drives the subscription habit
- Tiered: larger reward at 5/10/25 referrals

**Constraint:** rewarding at signup with a bot this easy to script invites abuse. → tracked as **P2-3**.

### I-3 · Watch-time analytics for admins
`chp_watch_history` accumulates rich data (per-episode, per-title, timestamps) surfaced today only as personal stats and ranks. An admin-facing view could show retention, drop-off points per serial, and which audio language actually gets watched — the last would directly inform which files are worth sourcing.

### I-4 · Use the audio-language filter to guide acquisition
Now that titles are filterable by audio track, the **empty results** are informative: a popular title with no `ru` file is a concrete gap. A report of "most-searched titles lacking language X" would turn a UI filter into a content-buying signal.

### I-5 · Notify users when a wanted title arrives
Related to I-4. If a user searches for something that does not exist or is unavailable in their language, capture that intent and notify them when it lands. Requires a new `chp_title_requests` table.

### I-6 · Public sharing links for titles
Deep links (`t.me/<bot>?start=title_<id>`) into a specific title. The referral deep-link machinery already parses `start` payloads, so the parsing half exists.

### I-7 · Trailer support
`Title` has poster and description but no trailer. A TMDB video lookup plus a `trailer_url` column would let the detail sheet lead with motion — likely a stronger conversion surface than a static poster.

### I-8 · Watchlist / "watch later" distinct from favorites
Favorites currently serve both "liked" and "want to watch". Splitting them would sharpen the recommendation signal in `recommended_for_user`, which today treats all history equally.

### I-9 · Improve recommendations beyond genre/type overlap
`recommended_for_user` matches on content type and genre overlap from watch history. It ignores **recency** (a film watched today weighs the same as one from months ago) and **completion** (abandoning after one episode counts as interest). Both are already in the data.

### I-13 · Let the viewer choose an audio track, not just see one
FR-7 as specified is informational: the detail sheet now lists which audio languages each episode has, and playback still picks one automatically via `pick_file`'s fallback chain. Where an episode genuinely has several — a Russian dub and an English original, say — the viewer can see both and get neither by choice.

Making the badges tappable would mean `/watch` accepting an audio language alongside `episode_id`, and `pick_file` honouring an explicit override rather than only the UI language. Small, and the data and UI are already in place. Deliberately not built in Phase 2: the request says *show*, and inventing the interaction was out of scope.

### I-10 · Let the Gemini catalog slice respect the audio filter
`AI_CATALOG_CONTEXT_LIMIT` sends 150 titles as AI context without regard to what the user can actually watch. Recommending a title with no file in their language is a dead end. Filtering the candidate slice through `_has_playable_file(user_language)` would make suggestions actionable.

### I-11 · Lazy-load the admin panel bundle
The admin panel ships inside the same 216 KB chunk every one of 508 users downloads, though only a handful are admins. → tracked as **P3-5**.

### I-12 · Language-preference reporting
`language_selected` now distinguishes a real choice from the `UZ` default. Legacy rows are "unknown", not a preference — worth reporting on once enough users have chosen, to decide whether Russian and English deserve continued translation effort. → tracked as **P3-2**.

---

## Promoted to TASKS.md

| Idea | Task |
|---|---|
| Neon branch as a dev/staging database | **P0-3** |
| Enforce `is_banned` and add an admin toggle | **P0-4** |
| Test suite, starting with auth and payments | **P1-1** |
| Automate the locale parity check | **P1-3** |
| Make balance spendable | **P2-1** |
| Order history (`orders.coming_soon` stub) | **P2-5** |
| Percentage-discount promo at checkout | **P2-6** |
| Mini App favorites / AI / parity | **P2-4** |
| Admin AI-usage report from the dormant `User` columns | **P3-1** |
| Audio filter in the bot's browse flow | **P3-3** |

---

## Superseded

- **Mini App keeping its own Uzbek strings** — replaced by the shared `app/locales/*.json` catalog served over `GET /api/i18n/{lang}`. Two copies drifted the moment one was forgotten. Shipped `9bd6d48`.
- **`ai_requests_today` / `ai_limit_reset_at` as the AI quota source of truth** — superseded by self-expiring Redis day-keys, which need no cron and no extra write per request. The columns survive only as a possible reporting source (I-1 → P3-1).
- **In-memory `asyncio.sleep` timers for auto-delete** — superseded by a Redis sorted-set delay queue; bare timers were lost on every redeploy. Both are now historical: auto-deletion was removed outright in Phase 3.
- **Per-episode audio filtering for continue-watching** — rejected in favor of the title-level `_has_playable_file` test. Filtering the joined episode would drop a half-watched serial whenever the exact episode someone stopped on lacked that track, even though the title is watchable.

---

## Rejected

- **Deleting the Telegram webhook on shutdown** — the webhook is global bot state, not one process's to release. A redeploy would leave the bot unreachable until the new instance booted, and a second environment shutting down last would wipe production's registration. Set on startup only. (`1f3192f`)
- **Bolting scheduled maintenance onto the web service's lifespan** — at the time, the auto-delete worker earned its place there by polling continuously (it has since been removed); monthly resets and stale-receipt expiry never did. Kept as a standalone idempotent script for a Render Cron Job, decoupled from web uptime and scaling.
- **Putting `GET /api/i18n/{lang}` behind `initData` auth** — the catalogs ship in the repository and contain no user data, and the first-open language picker needs them *before* auth resolves. Gating them would turn an auth hiccup into a blank screen instead of a degraded one.
