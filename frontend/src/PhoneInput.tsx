// A mobile-number field with a fixed "+91" country code, so a patient or
// staff member never has to type it (or forget it, or type it in one of
// several different shapes) -- the backend also normalizes independently
// (app/utils/phone.py) as the actual duplicate-patient safeguard, since
// this is a UI convenience, not the correctness boundary. Only digits are
// accepted for the local part, capped at 10 (a standard Indian mobile
// number's length), and the value this component reports upward is
// always the full "+91XXXXXXXXXX" string.
export default function PhoneInput({
  id,
  value,
  onChange,
  autoFocus,
}: {
  id?: string
  value: string
  onChange: (fullNumber: string) => void
  autoFocus?: boolean
}) {
  const localDigits = value.startsWith('+91') ? value.slice(3) : value.replace(/\D/g, '')

  function handleChange(raw: string) {
    const digits = raw.replace(/\D/g, '').slice(0, 10)
    onChange(digits ? `+91${digits}` : '')
  }

  return (
    <div className="phone-input">
      <span className="phone-input-prefix">+91</span>
      <input
        id={id}
        type="tel"
        inputMode="numeric"
        autoComplete="tel-national"
        placeholder="98765 43210"
        value={localDigits}
        onChange={(e) => handleChange(e.target.value)}
        autoFocus={autoFocus}
        maxLength={10}
        required
      />
    </div>
  )
}
