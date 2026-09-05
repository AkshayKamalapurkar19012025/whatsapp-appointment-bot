import { useEffect, useState } from 'react'
import { clearToken, getMe, getToken, logout } from './api'
import type { Patient } from './types'
import AppHeader from './AppHeader'
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

  // Client-side cleanup only -- used both after an explicit logout (see
  // handleLogout below, which calls the API first) and when a child
  // screen detects the session itself is already invalid (a 401 mid-
  // flow), where calling the logout API again would be pointless.
  function handleLoggedOut() {
    clearToken()
    setPatient(null)
    setView('booking')
  }

  async function handleLogout() {
    await logout().catch(() => undefined)
    handleLoggedOut()
  }

  if (checkingSession) {
    return (
      <>
        <AppHeader loggedIn={false} />
        <div className="page">
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading…
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <AppHeader
        loggedIn={patient !== null}
        primaryLabel={view === 'booking' ? 'My appointments' : 'Book an appointment'}
        onPrimaryAction={() => setView(view === 'booking' ? 'appointments' : 'booking')}
        onLogout={handleLogout}
      />
      <div className="page">
        {patient ? (
          view === 'booking' ? (
            <BookingFlow
              patientName={patient.name}
              onLoggedOut={handleLoggedOut}
              onViewAppointments={() => setView('appointments')}
            />
          ) : (
            <MyAppointments patientName={patient.name} onLoggedOut={handleLoggedOut} />
          )
        ) : (
          <LoginFlow onLoggedIn={handleLoggedIn} />
        )}
      </div>
    </>
  )
}
