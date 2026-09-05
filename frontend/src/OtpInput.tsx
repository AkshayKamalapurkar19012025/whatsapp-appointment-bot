import { useRef, type KeyboardEvent } from 'react'

// The OTP the backend issues is always exactly 6 digits, zero-padded
// (app/services/patient_auth.py's _generate_otp_code) -- this is purely a
// UI-layer decomposition of that one string into 6 boxes for a clearer,
// harder-to-mistype entry experience. It never changes the value LoginFlow
// sends to verifyOtp(): onChange still reports the same plain digit
// string a single <input> would have, just assembled from per-box
// keystrokes (or a single paste/autofill, which browsers deliver as one
// onChange with the full code) instead of typed into one field.
const LENGTH = 6

export default function OtpInput({
  value,
  onChange,
  autoFocus,
  disabled,
}: {
  value: string
  onChange: (value: string) => void
  autoFocus?: boolean
  disabled?: boolean
}) {
  const inputsRef = useRef<Array<HTMLInputElement | null>>([])
  const digits = Array.from({ length: LENGTH }, (_, i) => value[i] ?? '')

  function handleChange(index: number, raw: string) {
    const cleaned = raw.replace(/\D/g, '')
    if (cleaned.length > 1) {
      // A paste or autofill landed the whole code (or several digits) in
      // one box -- distribute it across the remaining boxes from here,
      // same as a native one-time-code field would.
      const next = digits.slice()
      for (let i = 0; i < cleaned.length && index + i < LENGTH; i++) {
        next[index + i] = cleaned[i]
      }
      onChange(next.join(''))
      inputsRef.current[Math.min(index + cleaned.length, LENGTH) - 1]?.focus()
      return
    }
    const next = digits.slice()
    next[index] = cleaned
    onChange(next.join(''))
    if (cleaned && index < LENGTH - 1) inputsRef.current[index + 1]?.focus()
  }

  function handleKeyDown(index: number, e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Backspace' && !digits[index] && index > 0) {
      inputsRef.current[index - 1]?.focus()
    } else if (e.key === 'ArrowLeft' && index > 0) {
      inputsRef.current[index - 1]?.focus()
    } else if (e.key === 'ArrowRight' && index < LENGTH - 1) {
      inputsRef.current[index + 1]?.focus()
    }
  }

  return (
    <div className="otp-input" role="group" aria-label="Verification code">
      {digits.map((digit, index) => (
        <input
          key={index}
          ref={(el) => {
            inputsRef.current[index] = el
          }}
          type="text"
          inputMode="numeric"
          autoComplete={index === 0 ? 'one-time-code' : 'off'}
          maxLength={LENGTH}
          value={digit}
          disabled={disabled}
          autoFocus={autoFocus && index === 0}
          onChange={(e) => handleChange(index, e.target.value)}
          onKeyDown={(e) => handleKeyDown(index, e)}
          aria-label={`Digit ${index + 1} of ${LENGTH}`}
          className="otp-input-box"
        />
      ))}
    </div>
  )
}
