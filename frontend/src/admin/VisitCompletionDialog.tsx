import { useEffect, useState } from 'react'
import { CheckCircle, Circle } from '@phosphor-icons/react'
import { ApiError, getVisitCompletionChecklist } from '../api'
import type { VisitCompletionChecklist } from '../types'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../components/ui/alert-dialog'

const CHECKLIST_ITEMS: { key: keyof VisitCompletionChecklist; label: string }[] = [
  { key: 'consultation_completed', label: 'Consultation completed' },
  { key: 'orders_created', label: 'Orders created' },
  { key: 'prescription_created', label: 'Prescription created' },
  { key: 'billing_completed', label: 'Billing completed' },
  { key: 'payment_completed', label: 'Payment completed' },
  { key: 'follow_up_scheduled', label: 'Follow-up scheduled' },
]

// The Visit Completion checklist (master spec section 43): a
// precondition SUMMARY shown right before the one action that closes
// out a visit, not a gate on it -- every item can be unchecked and the
// visit can still be completed (see the backend service's own
// docstring for why: Phase 11's Exception Engine already separately,
// continuously flags "billing not started"/"payment pending", this is
// the different, point-in-time summary the spec also asks for).
//
// Deliberately takes the plain appointmentId/patientName/doctorName
// it needs to render, not a whole AdminAppointment -- QueueSection.tsx's
// queue entries and AppointmentsPanel/DoctorWorkspace's admin
// appointments are two different shapes, and this is the one thing all
// three callers have in common.
export default function VisitCompletionDialog({
  appointmentId,
  patientName,
  doctorName,
  onClose,
  onConfirm,
}: {
  appointmentId: number
  patientName: string
  doctorName: string
  onClose: () => void
  // The caller's own existing completion action (e.g. runLifecycleAction
  // wrapping completeAdminAppointment) -- this dialog only decides
  // *when* that fires, not how it's called or how its own busy/error
  // state is shown.
  onConfirm: () => void
}) {
  const [checklist, setChecklist] = useState<VisitCompletionChecklist | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getVisitCompletionChecklist(appointmentId)
      .then((result) => {
        if (!cancelled) setChecklist(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load the completion checklist')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [appointmentId])

  const pendingCount = checklist ? CHECKLIST_ITEMS.filter((item) => !checklist[item.key]).length : 0

  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Complete OPD Visit</AlertDialogTitle>
          <AlertDialogDescription>
            {patientName}&apos;s visit with {doctorName}.{' '}
            {!loading &&
              !error &&
              checklist &&
              (pendingCount === 0
                ? 'Every step below is done.'
                : `${pendingCount} of ${CHECKLIST_ITEMS.length} steps ${pendingCount === 1 ? 'is' : 'are'} not done yet -- the visit can still be completed.`)}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {loading && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading visit status…
          </div>
        )}
        {error && <p className="error">{error}</p>}

        {checklist && (
          <ul className="visit-completion-checklist">
            {CHECKLIST_ITEMS.map((item) => {
              const done = checklist[item.key]
              return (
                <li key={item.key} className={done ? 'done' : 'pending'}>
                  {done ? <CheckCircle size={16} weight="fill" /> : <Circle size={16} />}
                  {item.label}
                </li>
              )
            })}
          </ul>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel>Not yet</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>Complete Visit</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
