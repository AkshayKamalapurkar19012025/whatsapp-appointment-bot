import { useEffect, useState } from 'react'
import { ApiError, requestOtp, setToken, verifyOtp } from './api'
import PhoneInput from './PhoneInput'

type Stage = 'number' | 'otp' | 'register'

// Purely a client-side "don't let someone hammer the button" guard --
// the backend's own real limit (3 requests per 10 minutes, see
// OTP_REQUEST_RATE_LIMIT_MAX/WINDOW_MINUTES in app/services/patient_
// auth.py) is what actually enforces anything; this cooldown is well
// inside that window and just paces the UI.
const RESEND_COOLDOWN_SECONDS = 30

export default function LoginFlow({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [stage, setStage] = useState<Stage>('number')
  const [whatsappNumber, setWhatsappNumber] = useState('')
  const [otp, setOtp] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [resendCooldown, setResendCooldown] = useState(0)
  const [resendMessage, setResendMessage] = useState<string | null>(null)

  useEffect(() => {
    if (resendCooldown <= 0) return
    const timer = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000)
    return () => clearTimeout(timer)
  }, [resendCooldown])

  async function handleRequestOtp(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await requestOtp(whatsappNumber)
      setStage('otp')
      setResendCooldown(RESEND_COOLDOWN_SECONDS)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send OTP')
    } finally {
      setBusy(false)
    }
  }

  async function handleResendOtp() {
    setError(null)
    setResendMessage(null)
    setBusy(true)
    try {
      await requestOtp(whatsappNumber)
      setResendCooldown(RESEND_COOLDOWN_SECONDS)
      setResendMessage('A new code was sent.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not resend the code')
    } finally {
      setBusy(false)
    }
  }

  async function submitOtp(withName?: string) {
    setError(null)
    setBusy(true)
    try {
      const result = await verifyOtp(whatsappNumber, otp, withName)
      if ('registration_required' in result) {
        setStage('register')
        return
      }
      setToken(result.session_token)
      onLoggedIn()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not verify OTP')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h1>Book an Appointment</h1>

      {stage === 'number' && (
        <form onSubmit={handleRequestOtp}>
          <label htmlFor="whatsapp_number">Mobile number</label>
          <PhoneInput id="whatsapp_number" value={whatsappNumber} onChange={setWhatsappNumber} autoFocus />
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={busy}>
            {busy ? 'Sending…' : 'Send OTP'}
          </button>
        </form>
      )}

      {stage === 'otp' && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            submitOtp()
          }}
        >
          <p>Enter the code sent to {whatsappNumber}</p>
          <label htmlFor="otp">Verification code</label>
          <input
            id="otp"
            inputMode="numeric"
            autoFocus
            value={otp}
            onChange={(e) => setOtp(e.target.value)}
            required
          />
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={busy}>
            {busy ? 'Verifying…' : 'Verify'}
          </button>

          <div className="otp-help">
            {resendMessage && !error && <p className="muted">{resendMessage}</p>}
            <button
              type="button"
              className="link"
              onClick={handleResendOtp}
              disabled={busy || resendCooldown > 0}
            >
              {resendCooldown > 0 ? `Resend code in ${resendCooldown}s` : 'Resend code'}
            </button>
            <button type="button" className="link" onClick={() => setStage('number')}>
              Use a different number
            </button>
            <p className="muted otp-fallback-hint">
              Still didn't get it? Contact the front desk — staff can book your appointment
              for you directly.
            </p>
          </div>
        </form>
      )}

      {stage === 'register' && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            submitOtp(name)
          }}
        >
          <p>We don't have an account for {whatsappNumber} yet. What's your name?</p>
          <label htmlFor="name">Full name</label>
          <input
            id="name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={busy}>
            {busy ? 'Creating account…' : 'Continue'}
          </button>
        </form>
      )}
    </div>
  )
}
