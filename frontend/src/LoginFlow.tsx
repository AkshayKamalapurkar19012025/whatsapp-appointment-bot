import { useState } from 'react'
import { ApiError, requestOtp, setToken, verifyOtp } from './api'
import PhoneInput from './PhoneInput'

type Stage = 'number' | 'otp' | 'register'

export default function LoginFlow({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [stage, setStage] = useState<Stage>('number')
  const [whatsappNumber, setWhatsappNumber] = useState('')
  const [otp, setOtp] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleRequestOtp(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await requestOtp(whatsappNumber)
      setStage('otp')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send OTP')
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
          <button type="button" className="link" onClick={() => setStage('number')}>
            Use a different number
          </button>
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
