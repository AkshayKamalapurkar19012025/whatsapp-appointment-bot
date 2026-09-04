import { useState } from 'react'
import MonthGrid from '../MonthGrid'
import { formatDate, isoDateOnly } from '../format'

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

// A plain calendar-grid date picker for administrative dates (recurring
// schedule From/Until, one-off block dates) -- reuses the same
// MonthGrid component the patient/admin slot-booking calendars use (see
// AdminCalendar.tsx), but with every date in the visible month enabled
// rather than driven by a real GET .../calendar availability lookup:
// these dates aren't about appointment-slot availability, just "which
// calendar day is this rule for". Past dates are greyed out (via the
// same `dates` unavailable styling MonthGrid already has) since none of
// these fields can meaningfully apply to a day that's already gone.
export default function AdminDatePicker({
  value,
  onChange,
  label = 'Pick from calendar',
}: {
  value: string
  onChange: (isoDate: string) => void
  label?: string
}) {
  const today = new Date()
  const todayIso = isoDateOnly(today.getFullYear(), today.getMonth() + 1, today.getDate())
  const [open, setOpen] = useState(false)
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)

  const isCurrentMonth = year === today.getFullYear() && month === today.getMonth() + 1

  const dates: Record<string, boolean> = {}
  const total = daysInMonth(year, month)
  for (let day = 1; day <= total; day++) {
    const iso = isoDateOnly(year, month, day)
    dates[iso] = iso >= todayIso
  }

  function goPrev() {
    if (isCurrentMonth) return
    const d = new Date(year, month - 2, 1)
    setYear(d.getFullYear())
    setMonth(d.getMonth() + 1)
  }

  function goNext() {
    const d = new Date(year, month, 1)
    setYear(d.getFullYear())
    setMonth(d.getMonth() + 1)
  }

  return (
    <div className="admin-date-picker">
      <button type="button" className="link" onClick={() => setOpen((v) => !v)}>
        {open ? 'Close calendar' : label}
      </button>
      {value && <span className="muted admin-date-picker-value">{formatDate(value)}</span>}
      {open && (
        <div className="admin-date-picker-panel">
          <MonthGrid
            year={year}
            month={month}
            dates={dates}
            onSelectDate={(iso) => {
              onChange(iso)
              setOpen(false)
            }}
            onPrevMonth={goPrev}
            onNextMonth={goNext}
            prevDisabled={isCurrentMonth}
            nextDisabled={false}
            showLegend={false}
          />
        </div>
      )}
    </div>
  )
}
