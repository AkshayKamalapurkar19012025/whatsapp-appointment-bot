import { useEffect, useState } from 'react'
import { Clock } from '@phosphor-icons/react'

// The viewer's own device clock, not the server's -- deliberately not an
// API call. Useful for spotting a timezone mismatch between what a
// patient's device thinks "now" is and what the app displays elsewhere
// (all doctor-facing times are the doctor's own local timezone, see
// app/utils/timezone.py), and it works identically on any device since
// it only ever reads the browser's own Date/Intl, never the network.
export default function LiveClock() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const time = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone

  return (
    <span className="live-clock" title={`Your device's local time (${zone})`}>
      <Clock size={13} weight="bold" aria-hidden="true" />
      {time}
    </span>
  )
}
