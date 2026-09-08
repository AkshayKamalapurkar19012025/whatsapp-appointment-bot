import { useEffect, useState } from 'react'
import { ApiError, completeAdminAppointment, getDoctorQueue } from '../api'
import type { DoctorQueue } from '../types'
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

export default function QueueSection({ doctorId }: { doctorId: number }) {
  const [queue, setQueue] = useState<DoctorQueue | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [completingId, setCompletingId] = useState<number | null>(null)

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

  async function handleComplete(appointmentId: number) {
    setError(null)
    setCompletingId(appointmentId)
    try {
      await completeAdminAppointment(appointmentId)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not mark the appointment completed')
    } finally {
      setCompletingId(null)
    }
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
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={completingId === queue.now_serving.appointment_id}
                  onClick={() => handleComplete(queue.now_serving!.appointment_id)}
                >
                  {completingId === queue.now_serving.appointment_id ? 'Saving…' : 'Mark completed'}
                </button>
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
                  </tr>
                </thead>
                <tbody>
                  {queue.waiting.map((entry) => (
                    <tr key={entry.appointment_id}>
                      <td>#{entry.token_number}</td>
                      <td>{entry.patient_name}</td>
                      <td>{formatTime(entry.visited_at)}</td>
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
                  </tr>
                </thead>
                <tbody>
                  {queue.completed.map((entry) => (
                    <tr key={entry.appointment_id}>
                      <td>#{entry.token_number}</td>
                      <td>{entry.patient_name}</td>
                      <td>{formatTime(entry.visited_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {queue.now_serving === null && queue.waiting.length === 0 && queue.completed.length === 0 && (
            <p className="muted">No one has checked in today yet.</p>
          )}
        </>
      )}
    </div>
  )
}
