import { useEffect, useState } from 'react'
import { clearToken, getMe, getToken } from './api'
import type { Patient } from './types'
import LoginFlow from './LoginFlow'
import BookingFlow from './BookingFlow'
import MyAppointments from './MyAppointments'
import './styles.css'

type View = 'booking' | 'appointments'

export default function App() {
  const [patient, setPatient] = useState<Patient | null>(null)
  const [checkingSession, setCheckingSession] = useState(true)
  const [view, setView] = useState<View>('booking')

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
    setView('booking')
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
        view === 'booking' ? (
          <BookingFlow
            patientName={patient.name}
            onLoggedOut={handleLoggedOut}
            onViewAppointments={() => setView('appointments')}
          />
        ) : (
          <MyAppointments
            patientName={patient.name}
            onLoggedOut={handleLoggedOut}
            onBookNew={() => setView('booking')}
          />
        )
      ) : (
        <LoginFlow onLoggedIn={handleLoggedIn} />
      )}
    </div>
  )
}
