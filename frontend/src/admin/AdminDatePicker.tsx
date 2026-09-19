import { useEffect, useRef, useState } from 'react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import { CalendarBlank, X } from '@phosphor-icons/react'
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
//
// The calendar panel is a Radix Popover portaled to document.body (same
// pattern TimeCombobox already uses), not an in-flow/absolutely-
// positioned child -- when this picker sits inside a scrollable
// container with a capped height (e.g. Configure Schedule's own
// .modal-panel), a plain `position: absolute` panel gets clipped at
// that container's overflow boundary instead of floating freely over
// the rest of the page.
export default function AdminDatePicker({
  value,
  onChange,
  placeholder = 'Select date',
}: {
  value: string
  onChange: (isoDate: string) => void
  placeholder?: string
}) {
  const today = new Date()
  const todayIso = isoDateOnly(today.getFullYear(), today.getMonth() + 1, today.getDate())
  const [open, setOpen] = useState(false)
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)
  const triggerRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

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

  // Radix's own outside-click dismissal (its DismissableLayer stack)
  // only ever reacts for the most-recently-opened layer -- with two
  // independent AdminDatePicker instances on the same page (Configure
  // Schedule's Start/End date pair), opening the second one leaves the
  // first sitting open indefinitely, even on a plain click elsewhere in
  // the modal. Same fix MonthGrid.tsx/usePreviewPopover already use
  // elsewhere in this app for the identical class of bug: a manual,
  // capture-phase document listener, checked against both the trigger
  // and the panel -- Popover.Portal renders the panel into
  // document.body, so it's never a DOM descendant of the trigger to
  // check containment against alone. onInteractOutside below hands
  // dismissal to this listener instead of Radix's own layer stack, so
  // there's exactly one mechanism deciding "is this click outside."
  useEffect(() => {
    if (!open) return
    function handleOutside(event: MouseEvent) {
      const target = event.target as Node
      if (triggerRef.current?.contains(target)) return
      if (panelRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('click', handleOutside, true)
    return () => document.removeEventListener('click', handleOutside, true)
  }, [open])

  return (
    <div className="admin-date-picker" ref={triggerRef}>
      <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
        <PopoverPrimitive.Anchor asChild>
          <button
            type="button"
            className={`admin-date-picker-trigger${value ? ' has-value' : ''}`}
            onClick={() => setOpen((v) => !v)}
            aria-label={value ? `${placeholder}, currently ${formatDate(value)}` : placeholder}
          >
            <CalendarBlank size={16} />
            <span>{value ? formatDate(value) : placeholder}</span>
          </button>
        </PopoverPrimitive.Anchor>
        {value && (
          <button
            type="button"
            className="admin-date-picker-clear"
            aria-label="Clear date"
            onClick={(e) => { e.stopPropagation(); onChange('') }}
          >
            <X size={14} />
          </button>
        )}
        <PopoverPrimitive.Portal>
          <PopoverPrimitive.Content
            ref={panelRef}
            side="bottom"
            align="start"
            sideOffset={4}
            onOpenAutoFocus={(e) => e.preventDefault()}
            onInteractOutside={(e) => e.preventDefault()}
            className="admin-date-picker-panel"
          >
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
          </PopoverPrimitive.Content>
        </PopoverPrimitive.Portal>
      </PopoverPrimitive.Root>
    </div>
  )
}
