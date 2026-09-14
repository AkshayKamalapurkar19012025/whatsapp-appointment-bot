import { useEffect, useState } from 'react'
import { DotsThree } from '@phosphor-icons/react'
import { ApiError, deleteDoctorBlock, getDoctorBlocks, getDoctorScheduleAdmin } from '../api'
import type { Doctor, DoctorBlockEntry, DoctorScheduleEntry } from '../types'
import { formatDate, formatTimeOfDay } from '../format'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../components/ui/alert-dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../components/ui/dropdown-menu'
import TimeOffModal from './TimeOffModal'
import {
  DEFAULT_MERGE_GAP_MINUTES,
  blockIsFullDayOnDate,
  blockLocalDateRange,
  dayAvailability,
  instantToLocalHHMM,
  mergeEntriesIntoBlocks,
  type ScheduleBlock,
} from './doctorSchedule'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}
function firstWeekdayColumn(year: number, month: number): number {
  const jsDay = new Date(year, month - 1, 1).getDay()
  return (jsDay + 6) % 7
}
function isoDate(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

// "17 Sep" or "25 – 27 Sep" -- the Upcoming Time Off list's compact
// date label, single date vs multi-day range.
function upcomingDateLabel(startDate: string, endDate: string): string {
  if (startDate === endDate) return formatDate(startDate)
  const startDay = new Date(`${startDate}T00:00:00`).getDate()
  return `${startDay} – ${formatDate(endDate)}`
}

// Doctor -> Time Off tab -- a monthly calendar (reusing the exact same
// day-status vocabulary the Schedule tab's own calendar uses: Working /
// Partial / Time off / No schedule, via dayAvailability in
// doctorSchedule.ts) plus a compact Upcoming Time Off list, replacing
// the old permanent inline date/start/end/reason/Save row. Every
// create/edit/remove happens through TimeOffModal, which persists
// straight through the EXISTING doctor_blocks API -- this component
// only fetches, renders, and decides which block (if any) a click
// should open for editing.
export default function TimeOffSection({ doctor }: { doctor: Doctor }) {
  const [scheduleEntries, setScheduleEntries] = useState<DoctorScheduleEntry[]>([])
  const [oneOffBlocks, setOneOffBlocks] = useState<DoctorBlockEntry[]>([])
  const [error, setError] = useState<string | null>(null)

  const today = new Date()
  const todayIso = isoDate(today.getFullYear(), today.getMonth() + 1, today.getDate())
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth() + 1)

  const [modalState, setModalState] = useState<{
    date: string
    forceAdd?: boolean
    forceEditBlock?: DoctorBlockEntry
  } | null>(null)
  const [removeTarget, setRemoveTarget] = useState<DoctorBlockEntry | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [showAll, setShowAll] = useState(false)

  function load() {
    Promise.all([getDoctorScheduleAdmin(doctor.id), getDoctorBlocks(doctor.id)])
      .then(([schedule, blocks]) => {
        setScheduleEntries(schedule)
        setOneOffBlocks(blocks)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load time off'))
  }
  useEffect(load, [doctor.id])

  const blocks: ScheduleBlock[] = mergeEntriesIntoBlocks(scheduleEntries, DEFAULT_MERGE_GAP_MINUTES)
  const activeOneOff = oneOffBlocks.filter((b) => b.active)

  function goPrevMonth() {
    setMonth((m) => (m === 1 ? (setYear((y) => y - 1), 12) : m - 1))
  }
  function goNextMonth() {
    setMonth((m) => (m === 12 ? (setYear((y) => y + 1), 1) : m + 1))
  }
  function goToday() {
    setYear(today.getFullYear())
    setMonth(today.getMonth() + 1)
  }

  // Clicking a date always opens the SAME TimeOffModal (never a second,
  // in-calendar popup) -- it decides for itself, from how many blocks
  // already cover that date, whether to show a plain add form, jump
  // straight into editing the one existing block, or show its "manage"
  // list for several. The page-level "+ Add Time Off" button and the
  // Upcoming list's own Edit action bypass that auto-detection
  // (forceAdd/forceEditBlock) since they already know exactly what they
  // want.
  function blocksForDate(dateStr: string): DoctorBlockEntry[] {
    return activeOneOff.filter((b) => {
      const { startDate, endDate } = blockLocalDateRange(b)
      return dateStr >= startDate && dateStr <= endDate
    })
  }
  function openForDate(dateStr: string) {
    setModalState({ date: dateStr })
  }
  function openAdd() {
    setModalState({ date: todayIso, forceAdd: true })
  }
  function openEdit(block: DoctorBlockEntry) {
    setModalState({ date: blockLocalDateRange(block).startDate, forceEditBlock: block })
  }
  function closeModal() {
    setModalState(null)
  }

  async function confirmRemove() {
    if (!removeTarget) return
    setRemoveBusy(true)
    try {
      await deleteDoctorBlock(doctor.id, removeTarget.id)
      setRemoveTarget(null)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove time off')
    } finally {
      setRemoveBusy(false)
    }
  }

  const totalDays = daysInMonth(year, month)
  const leadingBlanks = firstWeekdayColumn(year, month)
  const cells: (number | null)[] = [
    ...Array.from({ length: leadingBlanks }, () => null),
    ...Array.from({ length: totalDays }, (_, i) => i + 1),
  ]
  while (cells.length % 7 !== 0) cells.push(null)

  const upcomingAll = [...activeOneOff].sort((a, b) => a.start_at.localeCompare(b.start_at))
  const upcomingFuture = upcomingAll.filter((b) => blockLocalDateRange(b).endDate >= todayIso)
  const visibleUpcoming = showAll ? upcomingAll : upcomingFuture.slice(0, 6)

  return (
    <div>
      <div className="admin-content-header">
        <div>
          <h4 style={{ margin: 0 }}>Time Off</h4>
          <p className="muted" style={{ margin: 0 }}>
            Manage exceptions to the doctor's regular working hours.
          </p>
        </div>
        <button type="button" className="btn btn-sm" onClick={openAdd}>
          + Add Time Off
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="timeoff-layout">
        <div className="schedule-month-main">
          <div className="schedule-month-toolbar">
            <button type="button" className="btn-secondary btn btn-sm" onClick={goPrevMonth} aria-label="Previous month">
              {'‹'}
            </button>
            <span className="schedule-month-title">{MONTH_NAMES[month - 1]} {year}</span>
            <button type="button" className="btn-secondary btn btn-sm" onClick={goNextMonth} aria-label="Next month">
              {'›'}
            </button>
            <div className="schedule-month-toolbar-spacer" />
            <button type="button" className="btn-secondary btn btn-sm" onClick={goToday}>Today</button>
          </div>

          <div className="schedule-month-legend">
            <span className="schedule-legend-dot working" /> Working day
            <span className="schedule-legend-dot partial" /> Partial time off
            <span className="schedule-legend-dot time_off" /> Time off
            <span className="schedule-legend-dot none" /> No schedule
          </div>

          <div className="schedule-month-grid">
            {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((name) => (
              <div key={name} className="schedule-month-weekday">{name.slice(0, 3)}</div>
            ))}
            {cells.map((day, i) => {
              if (day === null) return <div key={i} className="schedule-month-cell empty" />
              const dateStr = isoDate(year, month, day)
              const { status, segments, workingBlocks } = dayAvailability(dateStr, blocks, activeOneOff)
              return (
                <button
                  key={i}
                  type="button"
                  className={`schedule-month-cell${dateStr === todayIso ? ' highlighted' : ''}`}
                  onClick={() => openForDate(dateStr)}
                >
                  <span className="schedule-month-cell-date">{day}</span>
                  {status === 'none' ? (
                    <span className="schedule-month-cell-none">No schedule</span>
                  ) : status === 'time_off' ? (
                    <span className="schedule-month-cell-timeoff">Time off</span>
                  ) : status === 'working' ? (
                    <span className="schedule-month-cell-periods">
                      {workingBlocks.slice(0, 2).map((b, bi) => (
                        <span key={bi} className="schedule-month-cell-period working">
                          {formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}
                        </span>
                      ))}
                      {workingBlocks.length > 2 && (
                        <span className="schedule-month-cell-more">+{workingBlocks.length - 2} more</span>
                      )}
                    </span>
                  ) : (
                    <span className="schedule-month-cell-periods">
                      <span className="schedule-month-cell-status partial">Partial time off</span>
                      {segments.slice(0, 2).map((s, si) => (
                        <span key={si} className={`schedule-month-cell-period ${s.type === 'time_off' ? 'time_off' : 'working'}`}>
                          {formatTimeOfDay(s.start)} – {formatTimeOfDay(s.end)}
                        </span>
                      ))}
                      {segments.length > 2 && <span className="schedule-month-cell-more">+{segments.length - 2} more</span>}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </div>

        <div className="timeoff-upcoming">
          <h4 style={{ margin: '0 0 var(--space-2)' }}>Upcoming Time Off</h4>
          {visibleUpcoming.length === 0 && <p className="muted">No time off scheduled.</p>}
          <ul className="timeoff-upcoming-list">
            {visibleUpcoming.map((b) => {
              const { startDate, endDate } = blockLocalDateRange(b)
              const fullDay = blockIsFullDayOnDate(b, startDate)
              return (
                <li key={b.id} className="timeoff-upcoming-item">
                  <div className="timeoff-upcoming-item-body">
                    <span className="timeoff-upcoming-date">{upcomingDateLabel(startDate, endDate)}</span>
                    <span className="timeoff-upcoming-time">
                      {fullDay ? 'Full day' : `${formatTimeOfDay(instantToLocalHHMM(b.start_at))} – ${formatTimeOfDay(instantToLocalHHMM(b.end_at))}`}
                    </span>
                    <span className="muted timeoff-upcoming-reason">{b.reason}</span>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button type="button" className="icon-btn" aria-label="Time off actions">
                        <DotsThree size={18} weight="bold" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => openEdit(b)}>Edit</DropdownMenuItem>
                      <DropdownMenuItem variant="danger" onSelect={() => setRemoveTarget(b)}>Remove</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </li>
              )
            })}
          </ul>
          {!showAll && upcomingAll.length > upcomingFuture.slice(0, 6).length && (
            <button type="button" className="btn-secondary btn btn-sm" style={{ width: '100%' }} onClick={() => setShowAll(true)}>
              View All Time Off
            </button>
          )}
        </div>
      </div>

      {modalState && (
        <TimeOffModal
          doctorId={doctor.id}
          blocks={blocks}
          initialDate={modalState.date}
          blocksForDate={blocksForDate(modalState.date)}
          forceAdd={modalState.forceAdd}
          forceEditBlock={modalState.forceEditBlock}
          onClose={closeModal}
          onSaved={load}
        />
      )}

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove Time Off?</AlertDialogTitle>
            <AlertDialogDescription>
              The doctor will become available during this period again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove} disabled={removeBusy}>
              {removeBusy ? 'Removing…' : 'Remove Time Off'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
