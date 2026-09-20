import { useEffect, useState } from 'react'
import { ApiError, getQueueDisplay } from './api'
import type { QueueDisplayEntry } from './types'

// Waiting-room display board -- meant for a lobby TV/kiosk browser, not
// a staff session. Lives at /display (see main.tsx), backed by GET
// /api/public/queue-display (app/api/queue_display.py), the one
// unauthenticated queue-reading endpoint in this app: it returns a bare
// doctor name + token number per active doctor, nothing that identifies
// which patient that number belongs to, so it's safe to leave this page
// open with no login on a screen anyone in the waiting room can see.
//
// Fixed dark palette rather than this app's usual --color-* theme
// tokens (styles.css) -- a public display board is meant to look the
// same regardless of the admin app's light/dark setting, since there's
// no "viewer preference" for a shared lobby screen.
const POLL_INTERVAL_MS = 5_000

export default function DisplayBoard() {
  const [doctors, setDoctors] = useState<QueueDisplayEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    function load() {
      getQueueDisplay()
        .then((result) => {
          setDoctors(result)
          setError(null)
        })
        .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the queue display'))
    }

    load()
    const interval = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="display-board">
      <div className="display-board-header">Now Serving</div>

      {error && <p className="display-board-error">{error}</p>}

      {doctors && doctors.length === 0 && <p className="display-board-empty">No doctors available.</p>}

      {doctors && doctors.length > 0 && (
        <div className="display-board-grid">
          {doctors.map((doctor) => (
            <div key={doctor.doctor_id} className="display-board-card">
              <div className="display-board-doctor-name">{doctor.doctor_name}</div>
              <div className="display-board-token">
                {doctor.now_serving_token !== null ? `#${doctor.now_serving_token}` : '—'}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
