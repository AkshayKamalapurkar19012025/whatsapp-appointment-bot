import * as React from 'react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import { cn } from '../../lib/utils'
import { formatTimeOfDay } from '../../format'

// A typeable time-of-day picker -- built for the Doctor Workspace
// Schedule tab's From/Until and break fields, which used to be a plain
// <select> full of every "HH:MM" option for the day. That was slow to
// scroll through with no way to jump to a time, and (as a native
// <select>) had no floating-position control of its own, so its
// dropdown could visually land on top of adjacent form fields.
//
// Options are generated from `durationMinutes` (the doctor's own
// configured slot duration) exactly as the old TimeOfDaySelect did --
// a 30-min doctor sees :00/:30 options, a 60-min doctor sees :00
// options, etc. -- and a previously-saved value that doesn't land on
// that grid is still injected as its own option rather than dropped, so
// switching duration later never silently rewrites a stored time.
//
// Positioning uses Radix's Popover (Portal + collision-aware floating
// placement), anchored to the text input, so the option list floats
// beside/below just this one field instead of covering the rest of the
// form -- unlike a native <select>'s browser-drawn dropdown, which this
// repo has no control over.

function minutesToHHMM(totalMinutes: number): string {
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

function timeOfDayOptions(durationMinutes: number, currentValue: string): string[] {
  const step = durationMinutes > 0 ? durationMinutes : 30
  const values: string[] = []
  for (let minutes = 0; minutes < 24 * 60; minutes += step) {
    values.push(minutesToHHMM(minutes))
  }
  if (!values.includes(currentValue)) {
    values.push(currentValue)
    values.sort()
  }
  return values
}

interface ParsedTimeQuery {
  hour: number
  minute?: number
  meridiem?: 'am' | 'pm'
}

// Accepts "9am", "9:00am", "9:00", "0900", "900", "17:00", "5pm" -- the
// query forms named in the spec, plus a few obvious variants (space
// before am/pm, bare 3-4 digit HHMM/HMM with no colon). Returns null for
// anything else, which falls back to a plain label substring match
// below rather than rejecting the keystroke outright.
function parseTimeQuery(raw: string): ParsedTimeQuery | null {
  const q = raw.trim().toLowerCase().replace(/\s+/g, '')
  if (!q) return null
  const match = /^(\d{1,2})(?::?(\d{2}))?(am|pm)?$/.exec(q)
  if (!match) return null
  const hour = Number(match[1])
  const minute = match[2] !== undefined ? Number(match[2]) : undefined
  const meridiem = match[3] as 'am' | 'pm' | undefined
  if (hour > 23 || (minute !== undefined && minute > 59)) return null
  return { hour, minute, meridiem }
}

// Which real 24-hour hour(s) a parsed, possibly-ambiguous query could
// mean. "9" or "9:00" with no am/pm matches both 9 AM and 9 PM (shown
// side by side until the user narrows it further); "17"/"17:00" is
// unambiguous 24-hour-style input; "12am"/"12pm" are the one pair where
// the 12-hour hour digit and the 24-hour hour disagree.
function candidateHours24(parsed: ParsedTimeQuery): number[] {
  const { hour, meridiem } = parsed
  if (hour >= 13) return [hour]
  if (hour === 12) return meridiem === 'am' ? [0] : meridiem === 'pm' ? [12] : [0, 12]
  if (hour === 0) return meridiem === 'pm' ? [12] : [0]
  return meridiem === 'am' ? [hour] : meridiem === 'pm' ? [hour + 12] : [hour, hour + 12]
}

function optionMatchesQuery(value: string, label: string, query: string): boolean {
  const trimmed = query.trim()
  if (!trimmed) return true
  const parsed = parseTimeQuery(trimmed)
  if (parsed) {
    const [valueHour, valueMinute] = value.split(':').map(Number)
    if (!candidateHours24(parsed).includes(valueHour)) return false
    if (parsed.minute !== undefined && parsed.minute !== valueMinute) return false
    return true
  }
  return label.toLowerCase().includes(trimmed.toLowerCase())
}

export function TimeCombobox({
  value,
  onChange,
  durationMinutes,
  ariaLabel,
}: {
  value: string
  onChange: (value: string) => void
  durationMinutes: number
  ariaLabel: string
}) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState('')
  const [highlightedIndex, setHighlightedIndex] = React.useState(0)
  const inputRef = React.useRef<HTMLInputElement>(null)

  const options = timeOfDayOptions(durationMinutes, value)
  const filtered = open ? options.filter((o) => optionMatchesQuery(o, formatTimeOfDay(o), query)) : options

  function openList() {
    setOpen(true)
    setQuery('')
    setHighlightedIndex(0)
  }

  function selectOption(v: string) {
    onChange(v)
    setOpen(false)
    setQuery('')
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      openList()
      return
    }
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightedIndex((i) => Math.min(i + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightedIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      // Inside a <form> -- must not submit it when the combobox itself
      // is what the Enter press was meant for.
      e.preventDefault()
      if (filtered[highlightedIndex]) selectOption(filtered[highlightedIndex])
    } else if (e.key === 'Escape') {
      setOpen(false)
      setQuery('')
    }
  }

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={(next) => (next ? openList() : setOpen(false))}>
      <PopoverPrimitive.Anchor asChild>
        <input
          ref={inputRef}
          type="text"
          aria-label={ariaLabel}
          required
          value={open ? query : formatTimeOfDay(value)}
          placeholder={formatTimeOfDay(value)}
          onFocus={openList}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
            setHighlightedIndex(0)
          }}
          onKeyDown={handleKeyDown}
          onBlur={() => {
            setOpen(false)
            setQuery('')
          }}
          className={cn(
            'w-[7.5rem] cursor-text rounded-[var(--radius-sm)] border border-[var(--color-border-strong)]',
            'bg-[var(--color-surface)] px-3 py-2 text-[0.95rem] text-[var(--color-text)]',
            'focus:outline-none focus:border-[var(--color-primary)] focus:ring-[3px] focus:ring-[var(--color-primary-soft)]',
          )}
        />
      </PopoverPrimitive.Anchor>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          side="bottom"
          align="start"
          sideOffset={4}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onCloseAutoFocus={(e) => e.preventDefault()}
          className={cn(
            // Higher than .modal-overlay's z-index:1000 (styles.css) --
            // this combobox is now also used inside ConfigureScheduleModal,
            // whose overlay would otherwise sit on top of and intercept
            // clicks on this floating dropdown (Tailwind's z-50 = z-index
            // 50 loses to the modal's plain-CSS z-index:1000, even though
            // both portal to document.body).
            'z-[1100] max-h-56 w-[7.5rem] overflow-y-auto rounded-[var(--radius-md)] p-1',
            'border border-[var(--color-border)] bg-[var(--color-surface)] shadow-[var(--shadow-lg)]',
          )}
        >
          {filtered.length === 0 && (
            <div className="px-3 py-2 text-[0.85rem] text-[var(--color-text-muted)]">No match</div>
          )}
          {filtered.map((v, i) => (
            <button
              key={v}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => selectOption(v)}
              className={cn(
                'flex w-full cursor-pointer select-none items-center rounded-[var(--radius-sm)] px-3 py-2 text-left',
                'text-[0.92rem] text-[var(--color-text)] outline-none',
                i === highlightedIndex && 'bg-[var(--color-primary-soft)] text-[var(--color-primary-hover)]',
                v === value && i !== highlightedIndex && 'font-semibold',
              )}
            >
              {formatTimeOfDay(v)}
            </button>
          ))}
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}
