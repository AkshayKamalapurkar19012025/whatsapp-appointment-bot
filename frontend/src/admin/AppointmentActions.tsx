import { DotsThreeVertical } from '@phosphor-icons/react'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../components/ui/dropdown-menu'
import type { AdminAppointment } from '../types'
import { hasStarted } from '../format'

export interface AppointmentActionHandlers {
  onConfirm: (a: AdminAppointment) => void
  onReject: (a: AdminAppointment) => void
  onCheckIn: (a: AdminAppointment) => void
  onNoShow: (a: AdminAppointment) => void
  onComplete: (a: AdminAppointment) => void
  onReschedule: (a: AdminAppointment) => void
  onCancel: (a: AdminAppointment) => void
  onViewDetails: (a: AdminAppointment) => void
  // Both just open the details view to this appointment (patient
  // arrival workflow Phase 5) -- neither is a single fire-and-forget
  // action like Confirm/Check-In, since collecting a payment needs a
  // method choice and waiving needs a reason, so there's no sensible
  // one-click version of either from the row.
  onCollectPayment: (a: AdminAppointment) => void
  onWaiveCharge: (a: AdminAppointment) => void
}

interface ActionDescriptor {
  key: string
  label: string
  onClick: () => void
  variant: 'primary' | 'secondary' | 'danger'
}

export interface AppointmentActionSet {
  primary: ActionDescriptor | null
  secondary: ActionDescriptor | null
  // "View details" plus whatever else doesn't fit as a primary/secondary
  // button for this status -- rendered as a single plain button when
  // there's only one (e.g. just "View details" for a settled
  // appointment), or a "..." menu once there's more than one, so a
  // status with three-plus applicable actions never has to wrap.
  overflow: ActionDescriptor[]
  // "Not started yet" for a Confirmed appointment whose time hasn't
  // arrived -- there's no lifecycle action to take yet, but this still
  // isn't nothing, so it's shown as a caption rather than silently
  // rendering an empty actions cell.
  note: string | null
}

// One status -> actions mapping, shared by the table row, the mobile
// card, and the details modal -- exactly the same set of buttons
// wherever an appointment is shown, never a second, drifting copy of
// "what can I do with a PENDING appointment" logic. Mirrors the
// existing per-status conditions this replaces in AppointmentsPanel.tsx
// (status/hasStarted combinations), not a new business rule.
//
// isAdmin gates "Waive Charge" specifically (patient arrival workflow
// Phase 3's ADMIN-only rule, enforced for real by the backend's
// require_role dependency -- hiding the button for STAFF here is a UX
// nicety, not the actual enforcement).
export function buildAppointmentActions(
  a: AdminAppointment,
  h: AppointmentActionHandlers,
  isAdmin: boolean,
): AppointmentActionSet {
  const viewDetails: ActionDescriptor = { key: 'details', label: 'View details', onClick: () => h.onViewDetails(a), variant: 'secondary' }
  const reschedule: ActionDescriptor = { key: 'reschedule', label: 'Reschedule', onClick: () => h.onReschedule(a), variant: 'secondary' }
  const cancel: ActionDescriptor = { key: 'cancel', label: 'Cancel', onClick: () => h.onCancel(a), variant: 'danger' }

  if (a.status === 'PENDING') {
    return {
      primary: { key: 'confirm', label: 'Confirm', onClick: () => h.onConfirm(a), variant: 'primary' },
      secondary: { key: 'reject', label: 'Reject', onClick: () => h.onReject(a), variant: 'danger' },
      overflow: [viewDetails, reschedule, cancel],
      note: null,
    }
  }

  if (a.status === 'CONFIRMED') {
    if (hasStarted(a.start_at)) {
      return {
        primary: { key: 'checkin', label: 'Check In', onClick: () => h.onCheckIn(a), variant: 'primary' },
        secondary: { key: 'noshow', label: 'No-Show', onClick: () => h.onNoShow(a), variant: 'danger' },
        overflow: [viewDetails, reschedule, cancel],
        note: null,
      }
    }
    return { primary: null, secondary: null, overflow: [viewDetails, reschedule, cancel], note: 'Not started yet' }
  }

  if (a.status === 'CHECKED_IN') {
    // Payment/waiver gate the queue (Phases 3-4): only once
    // payment_status leaves UNPAID/FAILED does "Mark completed" become
    // the right next action -- until then, the front desk still owes
    // either a payment or (ADMIN) a waiver.
    if (a.payment_status === 'UNPAID' || a.payment_status === 'FAILED') {
      return {
        primary: {
          key: 'collect-payment',
          label: a.payment_status === 'FAILED' ? 'Retry Payment' : 'Collect Payment',
          onClick: () => h.onCollectPayment(a),
          variant: 'primary',
        },
        secondary: isAdmin
          ? { key: 'waive-charge', label: 'Waive Charge', onClick: () => h.onWaiveCharge(a), variant: 'secondary' }
          : null,
        overflow: [viewDetails],
        note: null,
      }
    }
    return {
      primary: { key: 'complete', label: 'Mark completed', onClick: () => h.onComplete(a), variant: 'primary' },
      secondary: null,
      overflow: [viewDetails],
      note: null,
    }
  }

  // COMPLETED / CANCELLED / REJECTED / NO_SHOW -- settled, nothing left
  // to do but look at what happened.
  return { primary: null, secondary: null, overflow: [viewDetails], note: null }
}

export function AppointmentActionButtons({
  actions,
  busy,
  compact,
}: {
  actions: AppointmentActionSet
  busy: boolean
  // The appointments table row and mobile card pass this: only ONE
  // primary action shown inline, everything else (including what would
  // otherwise be the inline secondary button, e.g. Reject next to
  // Confirm) folds into the "..." overflow menu instead. Redesign spec:
  // "Do NOT display many text actions next to each other." The details
  // modal has room and omits this, so it still shows primary+secondary
  // side by side there.
  compact?: boolean
}) {
  const { primary, secondary, overflow: rawOverflow, note } = actions
  const showSecondaryInline = secondary && !compact
  const overflow = compact && secondary ? [secondary, ...rawOverflow] : rawOverflow

  return (
    <div className="appointment-actions-row">
      {note && <span className="muted appointment-actions-note">{note}</span>}
      {primary && (
        <button type="button" className="btn btn-sm" disabled={busy} onClick={primary.onClick}>
          {primary.label}
        </button>
      )}
      {showSecondaryInline && (
        <button
          type="button"
          className={secondary.variant === 'danger' ? 'btn-secondary btn-outline-danger btn btn-sm' : 'btn-secondary btn btn-sm'}
          disabled={busy}
          onClick={secondary.onClick}
        >
          {secondary.label}
        </button>
      )}
      {overflow.length === 1 && (
        <button type="button" className="btn-secondary btn btn-sm" onClick={overflow[0].onClick}>
          {overflow[0].label}
        </button>
      )}
      {overflow.length > 1 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className="overflow-menu-trigger" aria-label="More actions">
              <DotsThreeVertical size={18} weight="bold" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {overflow.map((item) => (
              <DropdownMenuItem key={item.key} variant={item.variant === 'danger' ? 'danger' : 'default'} onSelect={item.onClick}>
                {item.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}
