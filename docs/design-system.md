# Design System

The admin panel's colors live in one place: the `:root` block at the top of
`frontend/src/styles.css`. Everything else — buttons, pills, cards, charts,
the shadcn-style `components/ui/*` primitives — reads from these CSS custom
properties. Change a value here and the whole app updates; nothing below
should ever hardcode a hex value that already has a token meaning.

This document was written after an audit of the existing app (not a
rewrite): most of the token system below already existed and was already
followed consistently across the codebase. The audit's real findings —
listed at the end of this doc — were a handful of one-off hex colors that
had drifted from the tokens they meant to match, plus a few genuinely
missing tokens (`--color-info`, `--color-danger-hover`). See the session's
final report for the full list of files touched.

## Token reference

### Brand

| Token | Value | Use for |
|---|---|---|
| `--color-primary` | `#1f7a63` | Primary buttons, active nav, links, focus rings, the calendar/schedule's own "available/working" brand color |
| `--color-primary-hover` | `#185f4d` | Hover state for anything using `--color-primary` |
| `--color-primary-soft` | `#e3f3ee` | Primary's pale tint — hover backgrounds, selected-row backgrounds, default `.stat-icon` badge |
| `--color-primary-soft-strong` | `#c7e8dd` | A slightly stronger version of `-soft`, for two-tier emphasis within the same primary family |
| `--color-on-primary` | `#ffffff` | Text/icon color on top of a solid `--color-primary` fill |

The primary teal is the existing brand color, kept deliberately — this
pass refined and centralized the system around it rather than replacing
it with something new.

### Semantic status families

Four families, each with a solid tone (text/icons/borders) and a soft tone
(backgrounds):

| Family | Solid | Soft | Meaning |
|---|---|---|---|
| Success | `--color-success` `#1f7a4d` | `--color-success-soft` `#e4f5ea` | Available, Completed, Confirmed, Paid, saved successfully |
| Warning | `--color-warning` `#a5680a` | `--color-warning-soft` `#fdf2df` | Waiting, Pending, needs attention, approaching a limit |
| Danger | `--color-danger` `#b3261e` | `--color-danger-soft` `#fbe9e7` | Failed, Cancelled, Rejected, No Show, destructive actions |
| Info | `--color-info` `#2f6fed` | `--color-info-soft` `#e3edfc` | An active, in-progress workflow state (In Consultation, Arrived, an "existing" item in a comparison), or general informational context |

`--color-danger-hover` (`#8f1f19`) is the hover state for solid danger
buttons/actions specifically — it's its own token because, unlike primary,
danger appears as a solid fill in more than one place (`.btn-danger`, the
danger variant of the alert-dialog's confirm button) and both need to
agree on the same darker shade.

**A status never invents a fifth color family.** If a new status doesn't
obviously map to good/needs-attention/bad/in-progress, it's a sign the
status itself needs a rethink, not a new hex value — see "In Consultation"
in the status mapping table below for a worked example of resolving this.

### Neutral / surface hierarchy

| Token | Value | Use for |
|---|---|---|
| `--color-bg` | `#eef1f8` | Outermost page background (the gradient behind the app shell) |
| `--color-bg-subtle` | `#eef0f6` | Muted backgrounds inside a surface — disabled inputs, a stats card's inset panel, a timeline track |
| `--color-surface` | `#ffffff` | Cards, modals, table rows, the sidebar |
| `--color-surface-hover` | `#f7f8fc` | Hover background for a plain (non-primary-tinted) row/item |
| `--color-border` | `#e2e5f0` | Default hairline border |
| `--color-border-strong` | `#cdd2e4` | Input borders, anything that needs to read as more structural than a divider |
| `--color-text` | `#16211d` | Primary body text |
| `--color-text-secondary` | `#4b5a54` | Labels, secondary copy |
| `--color-text-muted` | `#7c8c85` | Placeholder text, captions, muted metadata — also doubles as the disabled-state text color; there is no separate `--color-text-disabled` token since every current disabled state already reads correctly in muted |

### Categorical accents — not status colors

| Token | Value |
|---|---|
| `--color-accent-purple` / `-soft` | `#7c4fd8` / `#ece4fb` |
| `--color-accent-rose` / `-soft` | `#c34a7c` / `#fbe4ee` |

`accent-teal`/`accent-blue`/`accent-amber`/`accent-purple`/`accent-rose`
(`cardAccent.ts`) are a small fixed palette hashed deterministically from a
department or appointment type's **name**, purely so a list of category
icons is individually distinguishable — teal and amber reuse the real
primary/warning tokens, purple and rose have their own tokens since
nothing semantic covers them. **These never carry status meaning.** A
purple accent icon next to "Cardiology" says nothing about whether
Cardiology is doing well or badly — it's just this department's assigned
color, the same way two different appointment types get two different
icon tints. Don't reach for these when what you actually need is a status
color, and don't reach for the semantic tokens when what you need is
"give this list item a distinct color" — that mixing is exactly what
produced the "In Consultation" purple bug fixed in this pass.

## Button hierarchy

| Class | Looks like | Used for |
|---|---|---|
| `.btn` (default) | Solid `--color-primary` fill | The one primary action on a screen/form: Save, Create, Confirm, Book, Generate, Continue |
| `.btn-secondary` | White with a border, primary-tinted on hover | Supporting actions: Cancel, Back, View, Edit |
| `.btn-danger` | Solid `--color-danger` fill | Destructive actions only: Delete, Deactivate, Cancel appointment, Remove |

Red is reserved for `.btn-danger`. An ordinary secondary action (Cancel a
dialog, Back, View) is `.btn-secondary`, never a red button — cancelling
out of a form isn't the same kind of action as cancelling an appointment.

## Status color mapping

Every status the app currently shows, and which family it belongs to.
"Real backend status" is the actual stored value; "Displayed as" is what
the UI shows it as, which is sometimes a further split of one backend
status into more specific buckets (see the OPD Today note below).

| Real backend status | Displayed as | Family |
|---|---|---|
| `PENDING` | Booked / Pending | Warning |
| `CONFIRMED` (not yet arrived) | Confirmed | Success |
| `CONFIRMED` + arrived early | Arrived early | Warning |
| `CHECKED_IN`, unpaid | Arrived (awaiting payment) | Info |
| `CHECKED_IN`, paid, not yet serving | Waiting | Warning |
| `CHECKED_IN`, paid, now serving (lowest token) | In Consultation | Info |
| `COMPLETED` | Completed | Success |
| `CANCELLED` | Cancelled | Danger |
| `REJECTED` | Rejected | Danger |
| `NO_SHOW` | No Show | Danger |
| `payment_status: PAID` / `WAIVED` | Paid / Waived | Success |
| `payment_status: UNPAID` | Due | Warning |
| `payment_status: FAILED` | Failed | Danger |
| Payment not applicable yet/at all | N/A | Neutral |
| Doctor `active: true` | Active | Success |
| Doctor `active: false` | Inactive | Neutral |

**Why "In Consultation" is Info blue, not purple.** The reference OPD Today
mockup this screen was built from used a one-off purple for "In
Consultation." This pass replaced it with `--color-info`, the same blue
used for "Arrived" and for the schedule conflict timeline's "existing
appointment" — because an in-progress consultation is exactly the "active
workflow state" the Info family exists for, and a purple used nowhere else
in the app is precisely the kind of per-page one-off color this system
exists to eliminate. If a future request wants a fifth, distinct family
purely for "actively happening right now" states, that's a deliberate
token addition (`--color-active` or similar), not a page-local hex value.

There is no `PARTIALLY PAID` status in the current data model —
`payment_status` is `UNPAID | PAID | FAILED | WAIVED | REFUNDED`, and
`record_payment_service` records one all-or-nothing charge or waiver per
appointment. The Payment column never fabricates a partial state; if
partial payments become a real feature, they'd need a backend field first,
then a fifth payment badge (Warning family, alongside Due) — not a
frontend-only guess at what "partial" should look like.

## What this pass changed vs. left alone

**Changed** (drifted one-off colors, now tokenized):
- `.accent-blue`, `.choice-card-date` — hardcoded `#e3edfc`/`#2f6fed` → `--color-info-soft`/`--color-info`
- `.accent-purple`, `.accent-rose` — hardcoded hex → `--color-accent-purple`/`--color-accent-rose` (+ soft variants)
- `.btn-danger:hover`, `alert-dialog.tsx`'s danger action hover — hardcoded `#8f1f19` → `--color-danger-hover`
- `.slots-timeline-chip.break` (Schedule preview) — a *different* red/pink (`#fdecea`/`#b3261e`) than the app's real danger-soft (`#fbe9e7`) for the same "this is a break" meaning → now the real tokens
- Schedule conflict timeline's "existing" bar/label — `#2563eb` → `--color-info`; "new"/"ok" — `#059669` (a second, different green than the app's real `--color-success`) → `--color-success`
- `--color-surface-muted` (referenced in three places, never actually defined in `:root` — every usage was silently falling back to its own inline hex) → `--color-bg-subtle`, the token that already covers this
- `--color-canvas` (same problem, one usage) → removed, replaced with the fallback value it was always resolving to anyway (`--color-surface`)
- Dead `var(--color-X, #fallback)` fallbacks throughout, where `#fallback` didn't match the real token's value (three different reds, for example) — removed; the token is always defined at `:root`, so these fallbacks never fired and only misdocumented the actual color
- `DashboardPanel.tsx`'s two chart lines — `#1f7a63` (already an exact match for `--color-primary`) and `#9bcddc` (an uncategorized light blue) → `var(--color-primary)` / `var(--color-info)`
- Dashboard's status stat-cards (Pending/Confirmed/Cancelled/Rejected/Completed/Visited) — every icon badge was the same plain primary teal regardless of whether the number was good or bad news → `.stat-icon` gained `.tone-success`/`.tone-warning`/`.tone-danger`/`.tone-info` modifiers, applied per status
- OPD Today's KPI icon badges and the Waiting/In Consultation status pills — this screen's own first pass (matching a reference screenshot) had introduced six new hardcoded hex values and a purple pill with no token backing; replaced with the shared `.stat-icon.tone-*` system and the real warning/info tokens

**Deliberately left alone:**
- The Schedule/calendar area's own use of plain `--color-primary`/`-soft`
  for "available/working" slots, legend dots, and month-cell periods. This
  reads as the calendar's everyday brand color, not a status badge — an
  entire calendar rendered in success-green would read as one continuous
  "success" alert rather than a calm scheduling grid, which the redesign
  brief's own "avoid making every element the same colour" and "the
  primary brand colour should primarily communicate interaction... while
  semantic colours communicate state" both argue against changing.
- `--color-primary` itself and every shared status pill already reused
  across multiple pages (`.status-confirmed`, `.status-arrived`,
  `.status-pending`, etc.) — these were already correct and already
  shared, so retinting them wasn't this pass's job.
- Disabled-state opacity, which currently varies by context (`0.55` for
  buttons, `0.6` for a schedule's doctor-select, `0.5` for icon buttons,
  `0.35` for calendar nav arrows at a month boundary). Two of the four
  have their own "this was deliberate" code comments explaining a
  context-specific reason for the lower opacity; unifying all four without
  understanding each one's reasoning risked a real, visible design
  regression for a purely cosmetic consistency gain. Recorded here as a
  known open question rather than force-changed.

## Remaining hardcoded colors

None outside the categories above. A full audit (`grep` across every
`.tsx`/`.ts`/`.css` file in `frontend/src`) after this pass found zero
remaining hex-literal colors that represent a semantic or brand concept.
The page background gradient (`.page`'s `linear-gradient(...)`) is the one
place hex values remain outside `:root`, and that's correct as-is — it's a
one-time decorative wash, not a reusable token, and inlining three
gradient stops into a token each used exactly once would be duplication in
the other direction.
