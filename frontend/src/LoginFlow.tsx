import { useEffect, useState } from 'react'
import { CalendarCheck, ChatCircleDots, Check, UserCircle } from '@phosphor-icons/react'
import { ApiError, requestOtp, setToken, verifyOtp } from './api'
import OtpInput from './OtpInput'
import PhoneInput from './PhoneInput'

type Stage = 'number' | 'otp' | 'register'

// Registration only branches off mid-flow (a brand new number lands on
// 'register' after OTP verification, not before), so the step strip
// below treats it as still part of "Verify" rather than adding a third
// step that would only sometimes exist.
const STEPS: { stage: Stage[]; label: string }[] = [
  { stage: ['number'], label: 'Phone' },
  { stage: ['otp', 'register'], label: 'Verify' },
]

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

  const HeaderIcon = stage === 'number' ? CalendarCheck : stage === 'otp' ? ChatCircleDots : UserCircle
  const currentStepIndex = STEPS.findIndex((step) => step.stage.includes(stage))

  return (
    <div className="login-shell">
      <div className="login-shell-glow" aria-hidden="true" />
      <div className="card patient-card login-card">
        <div className="login-icon-badge" aria-hidden="true">
          <HeaderIcon size={28} weight="light" />
        </div>
        <h1>Book an Appointment</h1>
        <p className="muted login-subtitle">Quick and secure — no password needed.</p>

        <div
          className="login-steps"
          role="progressbar"
          aria-valuenow={currentStepIndex + 1}
          aria-valuemin={1}
          aria-valuemax={STEPS.length}
        >
          {STEPS.map((step, index) => (
            <div
              key={step.label}
              className={`login-step${index === currentStepIndex ? ' current' : ''}${index < currentStepIndex ? ' done' : ''}`}
            >
              <span className="login-step-number" aria-hidden="true">
                {index < currentStepIndex ? <Check size={12} weight="bold" /> : index + 1}
              </span>
              <span className="login-step-label">{step.label}</span>
              {index < STEPS.length - 1 && <span className="login-step-connector" aria-hidden="true" />}
            </div>
          ))}
        </div>

        {stage === 'number' && (
          <form onSubmit={handleRequestOtp}>
            <label htmlFor="whatsapp_number">Mobile number</label>
            <PhoneInput id="whatsapp_number" value={whatsappNumber} onChange={setWhatsappNumber} autoFocus />
            <p className="muted login-help-text">We'll text a one-time code to verify it's you.</p>
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
            <span id="otp-label" className="field-label">
              Verification code
            </span>
            <OtpInput value={otp} onChange={setOtp} autoFocus disabled={busy} />
            {error && <p className="error">{error}</p>}
            <button type="submit" disabled={busy || otp.length !== 6}>
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
            <h2 className="register-heading">Let's get you set up</h2>
            <p className="muted register-subtext">
              We don't have an account for <strong>{whatsappNumber}</strong> yet — add your name to
              finish booking.
            </p>
            <label htmlFor="name">Full name</label>
            <input
              id="name"
              autoFocus
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <p className="muted login-help-text">This is how your appointments will be listed.</p>
            {error && <p className="error">{error}</p>}
            <button type="submit" disabled={busy}>
              {busy ? 'Creating account…' : 'Continue'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
