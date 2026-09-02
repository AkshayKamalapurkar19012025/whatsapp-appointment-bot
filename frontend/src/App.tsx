import { useEffect, useState } from 'react'
import { clearToken, getMe, getToken } from './api'
import type { Patient } from './types'
import LoginFlow from './LoginFlow'
import BookingFlow from './BookingFlow'
import './styles.css'

export default function App() {
  const [patient, setPatient] = useState<Patient | null>(null)
  const [checkingSession, setCheckingSession] = useState(true)

  useEffect(() => {
    if (!getToken()) {
      setCheckingSession(false)
      return
    }
    getMe()
      .then(setPatient)
      .catch(() => clearToken())
      .finally(() => setCheckingSession(false))
  }, [])

  function handleLoggedIn() {
    getMe()
      .then(setPatient)
      .catch(() => clearToken())
  }

  function handleLoggedOut() {
    clearToken()
    setPatient(null)
  }

  if (checkingSession) {
    return (
      <div className="page">
        <p>Loading…</p>
      </div>
    )
  }

  return (
    <div className="page">
      {patient ? (
        <BookingFlow patientName={patient.name} onLoggedOut={handleLoggedOut} />
      ) : (
        <LoginFlow onLoggedIn={handleLoggedIn} />
      )}
    </div>
  )
}
