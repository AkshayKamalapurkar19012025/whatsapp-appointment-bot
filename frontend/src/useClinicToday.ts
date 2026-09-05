import { useEffect, useState } from 'react'
import { getAppConfig } from './api'

// Same fallback/caching pattern as LiveClock.tsx -- see its own comment
// for why this is not "hardcoding a timezone" for display purposes.
const FALLBACK_TIMEZONE = 'Asia/Kolkata'

let cachedTimezone: Promise<string> | null = null

function loadTimezone(): Promise<string> {
  if (!cachedTimezone) {
    cachedTimezone = getAppConfig()
      .then((config) => config.default_timezone)
      .catch(() => FALLBACK_TIMEZONE)
  }
  return cachedTimezone
}

function todayIsoIn(timezone: string): string {
  // Parts-based reconstruction (not toLocaleDateString/toISOString)
  // for the same reason LiveClock.tsx builds its own date string by
  // hand: locale-dependent formatting and field ordering aren't
  // reliable, and toISOString() would silently convert through UTC
  // rather than the requested zone.
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
      .formatToParts(new Date())
      .map((part) => [part.type, part.value]),
  )
  return `${parts.year}-${parts.month}-${parts.day}`
}

// Today's date (YYYY-MM-DD), in the clinic's own configured timezone --
// not the viewer's device timezone. Used to mark "today" on a booking
// calendar: this is a display affordance only (which cell gets a
// "Today" ring), never the source of truth for which dates are
// actually bookable -- that's computed server-side, in each doctor's
// own timezone, by app/services/availability_engine.py.
export function useClinicToday(): string | null {
  const [today, setToday] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    function refresh() {
      loadTimezone().then((timezone) => {
        if (!cancelled) setToday(todayIsoIn(timezone))
      })
    }

    refresh()
    // Recomputed periodically (not just once) so a calendar left open
    // across midnight eventually catches up -- date-granularity, so
    // this doesn't need LiveClock's much shorter tick interval.
    const id = setInterval(refresh, 60000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return today
}
