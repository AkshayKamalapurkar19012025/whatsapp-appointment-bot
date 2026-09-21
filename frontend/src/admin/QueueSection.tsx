import { useEffect, useState } from 'react'
import {
  ApiError,
  completeAdminAppointment,
  getDoctorQueue,
  holdQueueEntry,
  recallQueueEntry,
  setQueuePriority,
} from '../api'
import type { DoctorQueue, QueueEntry } from '../types'
import { formatTime } from '../format'

// Today's walk-in queue (ADMIN or STAFF) -- migrations/0012_appointment_
// queue_tokens.sql, moved to fire on payment/waiver rather than
// check-in itself as of the patient arrival workflow's Phase 4. A
// token number appears here only once a patient has actually paid or
// been waived (see get_doctor_queue's own token_number IS NOT NULL
// filter) -- a checked-in-but-unpaid patient never shows up, matching
// the core business rule.
//
// Extracted out of DoctorDetail.tsx (where it started as one profile
// tab) so QueuePanel.tsx -- a standalone, doctor-selectable queue
// screen -- can reuse the exact same component instead of a second,
// drifting copy. DoctorDetail's own Queue tab still renders this same
// component unchanged.
//
// Polls every 20s while mounted: this is meant to be a screen staff or
// a doctor leaves open during a shift, not something re-opened by hand
// to see the next patient. Cleared on unmount/doctor change so a
// background tab doesn't keep polling after navigating away.
const POLL_INTERVAL_MS = 20_000

export default function QueueSection({
  doctorId,
  onOpenConsultation,
}: {
  doctorId: number
  // OPD/HIMS master spec Phase 5 -- opens ConsultationWorkspace for a
  // given appointment. Optional so QueueSection keeps working for any
  // caller that hasn't wired navigation to it (there is none left
  // today, but this component is reused across DoctorWorkspace's
  // Overview tab and the standalone QueuePanel -- see this phase's
  // report).
  onOpenConsultation?: (appointmentId: number) => void
}) {
  const [queue, setQueue] = useState<DoctorQueue | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // One row at a time can be "doing something" -- covers Mark
  // completed/Skip/Recall/Priority alike, since they're mutually
  // exclusive per row and this keeps the busy-state plumbing to one
  // variable instead of four drifting copies.
  const [busyId, setBusyId] = useState<number | null>(null)
  // The one row currently showing the "why is this priority" reason
  // input, opened by clicking Priority -- see startPriority/
  // confirmPriority below. Not just a boolean: only one row's form is
  // open at a time, and this doubles as which one.
  const [priorityTargetId, setPriorityTargetId] = useState<number | null>(null)
  const [priorityReason, setPriorityReason] = useState('')

  function load() {
    getDoctorQueue(doctorId)
      .then(setQueue)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the queue'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    setLoading(true)
    setError(null)
    load()
    const interval = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doctorId])

  async function runAction(appointmentId: number, action: () => Promise<unknown>, failureMessage: string) {
    setError(null)
    setBusyId(appointmentId)
    try {
      await action()
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failureMessage)
    } finally {
      setBusyId(null)
    }
  }

  function handleComplete(appointmentId: number) {
    return runAction(appointmentId, () => completeAdminAppointment(appointmentId), 'Could not mark the appointment completed')
  }

  function handleHold(appointmentId: number) {
    return runAction(appointmentId, () => holdQueueEntry(appointmentId), 'Could not skip this patient')
  }

  function handleRecall(appointmentId: number) {
    return runAction(appointmentId, () => recallQueueEntry(appointmentId), 'Could not recall this patient')
  }

  function startPriority(appointmentId: number) {
    setPriorityTargetId(appointmentId)
    setPriorityReason('')
  }

  function cancelPriority() {
    setPriorityTargetId(null)
    setPriorityReason('')
  }

  async function confirmPriority(appointmentId: number) {
    if (!priorityReason.trim()) return
    await runAction(
      appointmentId,
      () => setQueuePriority(appointmentId, true, priorityReason.trim()),
      'Could not mark this patient priority',
    )
    setPriorityTargetId(null)
    setPriorityReason('')
  }

  function handleRemovePriority(appointmentId: number) {
    return runAction(appointmentId, () => setQueuePriority(appointmentId, false), 'Could not remove priority')
  }

  // Shared by the Waiting and Held tables -- Priority/Remove priority
  // and Skip/Recall are the same actions in both, just swapping which
  // one applies depending on whether this row is currently held.
  function renderActions(entry: QueueEntry, held: boolean) {
    if (priorityTargetId === entry.appointment_id) {
      return (
        <div className="queue-priority-form">
          <input
            type="text"
            placeholder="Reason (required)"
            value={priorityReason}
            onChange={(e) => setPriorityReason(e.target.value)}
            autoFocus
          />
          <button
            type="button"
            className="btn btn-sm"
            disabled={!priorityReason.trim() || busyId === entry.appointment_id}
            onClick={() => confirmPriority(entry.appointment_id)}
          >
            {busyId === entry.appointment_id ? 'Saving…' : 'Confirm'}
          </button>
          <button type="button" className="btn-secondary btn btn-sm" onClick={cancelPriority}>
            Cancel
          </button>
        </div>
      )
    }

    const busy = busyId === entry.appointment_id

    return (
      <div className="queue-row-actions">
        {onOpenConsultation && (
          <button
            type="button"
            className="btn-secondary btn btn-sm"
            onClick={() => onOpenConsultation(entry.appointment_id)}
          >
            Consultation
          </button>
        )}
        {held ? (
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => handleRecall(entry.appointment_id)}>
            {busy ? 'Saving…' : 'Recall'}
          </button>
        ) : (
          <button
            type="button"
            className="btn-secondary btn btn-sm"
            disabled={busy}
            onClick={() => handleHold(entry.appointment_id)}
          >
            {busy ? 'Saving…' : 'Skip'}
          </button>
        )}
        {entry.is_priority ? (
          <button
            type="button"
            className="btn-secondary btn btn-sm"
            disabled={busy}
            onClick={() => handleRemovePriority(entry.appointment_id)}
          >
            Remove priority
          </button>
        ) : (
          <button
            type="button"
            className="btn-secondary btn btn-sm"
            disabled={busy}
            onClick={() => startPriority(entry.appointment_id)}
          >
            Priority
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="detail-section">
      <h4>Today&apos;s queue</h4>
      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}

      {!loading && queue && (
        <>
          <div className="queue-now-serving">
            <span className="queue-now-serving-label">Now serving</span>
            {queue.now_serving ? (
              <>
                <span className="queue-token-badge">#{queue.now_serving.token_number}</span>
                <span>{queue.now_serving.patient_name}</span>
                {queue.now_serving.is_priority && <span className="queue-priority-badge">Priority</span>}
                <div className="queue-now-serving-actions">
                  {renderActions(queue.now_serving, false)}
                  {priorityTargetId !== queue.now_serving.appointment_id && (
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={busyId === queue.now_serving.appointment_id}
                      onClick={() => handleComplete(queue.now_serving!.appointment_id)}
                    >
                      {busyId === queue.now_serving.appointment_id ? 'Saving…' : 'Mark completed'}
                    </button>
                  )}
                </div>
              </>
            ) : (
              <span className="muted">Nobody waiting right now.</span>
            )}
          </div>

          {queue.waiting.length > 0 && (
            <>
              <h4>Waiting ({queue.waiting.length})</h4>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Token</th>
                    <th>Patient</th>
                    <th>Checked in</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {queue.waiting.map((entry) => (
                    <tr key={entry.appointment_id}>
                      <td>#{entry.token_number}</td>
                      <td>
                        {entry.patient_name}
                        {entry.is_priority && <span className="queue-priority-badge">Priority</span>}
                      </td>
                      <td>{formatTime(entry.visited_at)}</td>
                      <td>{renderActions(entry, false)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {queue.held.length > 0 && (
            <>
              <h4>Held ({queue.held.length})</h4>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Token</th>
                    <th>Patient</th>
                    <th>Checked in</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {queue.held.map((entry) => (
                    <tr key={entry.appointment_id}>
                      <td>#{entry.token_number}</td>
                      <td>
                        {entry.patient_name}
                        {entry.is_priority && <span className="queue-priority-badge">Priority</span>}
                      </td>
                      <td>{formatTime(entry.visited_at)}</td>
                      <td>{renderActions(entry, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {queue.completed.length > 0 && (
            <>
              <h4>Completed today ({queue.completed.length})</h4>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Token</th>
                    <th>Patient</th>
                    <th>Checked in</th>
                    {onOpenConsultation && <th>Actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {queue.completed.map((entry) => (
                    <tr key={entry.appointment_id}>
                      <td>#{entry.token_number}</td>
                      <td>{entry.patient_name}</td>
                      <td>{formatTime(entry.visited_at)}</td>
                      {onOpenConsultation && (
                        <td>
                          <button
                            type="button"
                            className="btn-secondary btn btn-sm"
                            onClick={() => onOpenConsultation(entry.appointment_id)}
                          >
                            View consultation
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {queue.now_serving === null &&
            queue.waiting.length === 0 &&
            queue.held.length === 0 &&
            queue.completed.length === 0 && <p className="muted">No one has checked in today yet.</p>}
        </>
      )}
    </div>
  )
}
