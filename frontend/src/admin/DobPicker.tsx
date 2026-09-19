import { useEffect, useState } from 'react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import { CalendarBlank, CaretLeft, CaretRight } from '@phosphor-icons/react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import { formatDobForInput, formatPreciseAge, isoDateOnly, parseTypedDob, toIsoDate } from '../format'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const WEEKDAY_LABELS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

// Sunday-first column index, matching the ASCII wireframe in the OPD
// Patient Search & Registration redesign report ("Su Mo Tu We Th Fr
// Sa") -- deliberately NOT MonthGrid.tsx's Monday-first convention.
// This is a fresh, purpose-built grid rather than a MonthGrid reuse:
// MonthGrid is tightly coupled to slot-availability semantics (hover
// popovers of existing appointments, an available/unavailable legend)
// that don't apply to a birth date, where every past day is equally
// selectable and only "future" is ever disabled.
function firstWeekdayColumn(year: number, month: number): number {
  return new Date(year, month - 1, 1).getDay()
}

const DECADE_SIZE = 12

// A fast, purpose-built DOB control for a hospital front desk -- see
// the OPD Patient Search & Registration redesign report, point 2/3:
// registering a patient born in 1945 must never mean clicking a
// previous-month arrow hundreds of times. Two independent entry paths,
// always both available (never picker-only):
//
// 1. Direct typed entry ("DD/MM/YYYY") in the visible text field --
//    the fastest path for a receptionist who already knows the date.
// 2. The calendar-icon button opens a panel with direct month+year
//    selection (Select dropdowns, no incremental nav required) plus a
//    year-grid "jump to decade" view with its own typed year search,
//    for a receptionist who needs to browse.
//
// Both paths converge on the same onChange(isoDate) -- the parent
// (PatientFormModal) never needs to know which one was used.
export default function DobPicker({
  id,
  value,
  onChange,
  placeholder = 'DD/MM/YYYY',
}: {
  id?: string
  value: string
  onChange: (isoDate: string) => void
  placeholder?: string
}) {
  const today = new Date()
  const todayIso = toIsoDate(today)

  const [text, setText] = useState(value ? formatDobForInput(value) : '')
  const [typedError, setTypedError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<'days' | 'years'>('days')
  const [viewYear, setViewYear] = useState(() => (value ? Number(value.slice(0, 4)) : today.getFullYear()))
  const [viewMonth, setViewMonth] = useState(() => (value ? Number(value.slice(5, 7)) : today.getMonth() + 1))
  const [decadeStart, setDecadeStart] = useState(() => viewYear - (viewYear % DECADE_SIZE))
  const [yearJump, setYearJump] = useState('')

  // Keeps the typed field in sync with a value picked via the calendar
  // (or set by the parent directly, e.g. loading an existing patient
  // for edit) -- never fights the user's own keystrokes, since this
  // only runs when `value` itself changes, not on every render.
  useEffect(() => {
    setText(value ? formatDobForInput(value) : '')
    setTypedError(null)
  }, [value])

  function commitTyped(raw: string) {
    const trimmed = raw.trim()
    if (!trimmed) {
      setTypedError(null)
      onChange('')
      return
    }
    const iso = parseTypedDob(trimmed)
    if (!iso) {
      setTypedError('Enter a valid date as DD/MM/YYYY')
      return
    }
    if (iso > todayIso) {
      setTypedError('Date of birth cannot be in the future')
      return
    }
    setTypedError(null)
    onChange(iso)
  }

  function openPicker() {
    const base = value || todayIso
    setViewYear(Number(base.slice(0, 4)))
    setViewMonth(Number(base.slice(5, 7)))
    setView('days')
    setOpen(true)
  }

  function selectDay(day: number) {
    const iso = isoDateOnly(viewYear, viewMonth, day)
    if (iso > todayIso) return
    onChange(iso)
    setOpen(false)
  }

  function goToday() {
    onChange(todayIso)
    setOpen(false)
  }

  function openYearGrid() {
    setDecadeStart(viewYear - (viewYear % DECADE_SIZE))
    setView('years')
  }

  function pickYear(year: number) {
    setViewYear(year)
    setView('days')
  }

  function jumpToTypedYear() {
    const year = Number(yearJump)
    if (!Number.isInteger(year) || year < 1900 || year > today.getFullYear()) return
    setViewYear(year)
    setYearJump('')
    setView('days')
  }

  const totalDays = daysInMonth(viewYear, viewMonth)
  const leadingBlanks = firstWeekdayColumn(viewYear, viewMonth)
  const cells: Array<number | null> = []
  for (let i = 0; i < leadingBlanks; i++) cells.push(null)
  for (let day = 1; day <= totalDays; day++) cells.push(day)

  const preciseAge = value ? formatPreciseAge(value) : null

  return (
    <div className="dob-picker">
      <div className="dob-picker-input-row">
        <input
          id={id}
          type="text"
          inputMode="numeric"
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => commitTyped(text)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commitTyped(text)
            }
          }}
          aria-label="Date of birth"
          aria-invalid={typedError ? true : undefined}
        />
        <PopoverPrimitive.Root open={open} onOpenChange={(v) => { setOpen(v); if (v) openPicker() }}>
          <PopoverPrimitive.Trigger asChild>
            <button type="button" className="dob-picker-trigger" aria-label="Open date of birth picker">
              <CalendarBlank size={17} weight="bold" />
            </button>
          </PopoverPrimitive.Trigger>
          <PopoverPrimitive.Portal>
            <PopoverPrimitive.Content
              side="bottom"
              align="start"
              sideOffset={4}
              className="dob-picker-panel"
              onOpenAutoFocus={(e) => e.preventDefault()}
            >
              {view === 'days' && (
                <>
                  <div className="dob-picker-nav">
                    <Select value={String(viewMonth)} onValueChange={(v) => setViewMonth(Number(v))}>
                      <SelectTrigger className="dob-picker-month-select">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {MONTH_NAMES.map((name, index) => (
                          <SelectItem key={name} value={String(index + 1)}>
                            {name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <button type="button" className="dob-picker-year-button" onClick={openYearGrid}>
                      {viewYear}
                    </button>
                  </div>

                  <div className="dob-picker-grid">
                    {WEEKDAY_LABELS.map((label) => (
                      <div key={label} className="dob-picker-weekday">
                        {label}
                      </div>
                    ))}
                    {cells.map((day, index) => {
                      if (day === null) return <div key={`blank-${index}`} className="dob-picker-day empty" />
                      const iso = isoDateOnly(viewYear, viewMonth, day)
                      const isFuture = iso > todayIso
                      const isSelected = value === iso
                      const isToday = todayIso === iso
                      return (
                        <button
                          key={iso}
                          type="button"
                          className={`dob-picker-day${isSelected ? ' selected' : ''}${isToday ? ' today' : ''}`}
                          disabled={isFuture}
                          onClick={() => selectDay(day)}
                        >
                          {day}
                        </button>
                      )
                    })}
                  </div>

                  <div className="dob-picker-footer">
                    <button type="button" className="link" onClick={goToday}>
                      Today
                    </button>
                    <button type="button" className="btn-secondary btn btn-sm" onClick={() => setOpen(false)}>
                      Done
                    </button>
                  </div>
                </>
              )}

              {view === 'years' && (
                <>
                  <div className="dob-picker-nav">
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label="Previous decade"
                      onClick={() => setDecadeStart((d) => d - DECADE_SIZE)}
                    >
                      <CaretLeft size={15} />
                    </button>
                    <span className="dob-picker-decade-label">
                      {decadeStart} – {decadeStart + DECADE_SIZE - 1}
                    </span>
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label="Next decade"
                      disabled={decadeStart + DECADE_SIZE > today.getFullYear()}
                      onClick={() => setDecadeStart((d) => d + DECADE_SIZE)}
                    >
                      <CaretRight size={15} />
                    </button>
                  </div>

                  <div className="dob-picker-year-grid">
                    {Array.from({ length: DECADE_SIZE }, (_, i) => decadeStart + i).map((year) => (
                      <button
                        key={year}
                        type="button"
                        className={`dob-picker-year-cell${year === viewYear ? ' selected' : ''}`}
                        disabled={year > today.getFullYear()}
                        onClick={() => pickYear(year)}
                      >
                        {year}
                      </button>
                    ))}
                  </div>

                  <div className="dob-picker-year-search">
                    <label>
                      Jump to year
                      <input
                        type="number"
                        inputMode="numeric"
                        placeholder={String(today.getFullYear() - 40)}
                        value={yearJump}
                        onChange={(e) => setYearJump(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault()
                            jumpToTypedYear()
                          }
                        }}
                      />
                    </label>
                    <button type="button" className="btn-secondary btn btn-sm" onClick={jumpToTypedYear}>
                      Go
                    </button>
                  </div>
                </>
              )}
            </PopoverPrimitive.Content>
          </PopoverPrimitive.Portal>
        </PopoverPrimitive.Root>
      </div>

      {typedError && <p className="dob-picker-error">{typedError}</p>}
      {!typedError && preciseAge && (
        <p className="dob-picker-age">
          Age <strong>{preciseAge}</strong>
        </p>
      )}
    </div>
  )
}
