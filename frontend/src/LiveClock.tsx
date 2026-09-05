import { useEffect, useState } from 'react'
import { Clock } from '@phosphor-icons/react'
import { getAppConfig } from './api'

// Falls back to the same default the backend itself ships with (see
// app/config.py's DEFAULT_TIMEZONE) until the real value has loaded --
// this is not "hardcoding a timezone" for display purposes, it's just
// the initial-render placeholder before the one-time fetch below
// resolves, and it's always overwritten by whatever the backend
// actually returns.
const FALLBACK_TIMEZONE = 'Asia/Kolkata'

// Fetched once per page load and shared by every LiveClock instance
// (there is normally only ever one mounted at a time) rather than once
// per mount, since the value never changes during a session.
let cachedTimezone: Promise<string> | null = null

function loadTimezone(): Promise<string> {
  if (!cachedTimezone) {
    cachedTimezone = getAppConfig()
      .then((config) => config.default_timezone)
      .catch(() => FALLBACK_TIMEZONE)
  }
  return cachedTimezone
}

// The clinic's own configured timezone (app.config.DEFAULT_TIMEZONE), not
// the viewer's device timezone -- deliberately not derived from the
// browser's Intl.DateTimeFormat().resolvedOptions().timeZone, since a
// patient booking from a different timezone should still see the
// clinic's own current date/time, matching what every doctor-facing time
// elsewhere in the app already means (see app/utils/timezone.py's
// get_doctor_timezone). Still never an API call for the ticking clock
// itself -- only the timezone name is fetched (once); the time is always
// derived from the browser's own Date.now(), just formatted into that
// timezone.
export default function LiveClock() {
  const [timezone, setTimezone] = useState(FALLBACK_TIMEZONE)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    loadTimezone().then(setTimezone)
  }, [])

  useEffect(() => {
    // No seconds are displayed, so once a minute is often enough -- but
    // ticking every 15s keeps the display from ever looking "stuck" for
    // up to a full minute right after mount.
    const id = setInterval(() => setNow(new Date()), 15000)
    return () => clearInterval(id)
  }, [])

  // Built from individual parts (locale fixed to en-US, day/month/year
  // pulled out and reassembled in that exact order) rather than handed to
  // toLocaleDateString/toLocaleString wholesale -- otherwise both the
  // day-month-year ordering and the month spelling would follow the
  // viewer's own browser locale (en-GB's "short" month is "Sept", not the
  // 3-letter "Sep" asked for; a en-US browser would order it "Sep 05,
  // 2026" instead). AM/PM is likewise forced via hour12, independent of
  // locale-driven 24-hour defaults.
  const dateParts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', { day: '2-digit', month: 'short', year: 'numeric', timeZone: timezone })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  )
  const date = `${dateParts.day} ${dateParts.month} ${dateParts.year}`
  const time = now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: timezone })

  return (
    <span className="live-clock" title={`Current time in the clinic's timezone (${timezone})`}>
      <Clock size={13} weight="bold" aria-hidden="true" />
      {date} · {time}
    </span>
  )
}
