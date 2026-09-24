import { useEffect, useState, type CSSProperties } from 'react'
import { Buildings, CalendarCheck, MagnifyingGlass, Stethoscope, UsersThree } from '@phosphor-icons/react'
import {
  ApiError,
  getDoctorBlocks,
  getDoctorScheduleAdmin,
  listAdminAppointments,
  listAllDoctors,
  listDepartments,
  listDoctorsInDepartment,
} from '../api'
import type { AdminAppointment, Department, Doctor, DoctorBlockEntry, DoctorScheduleEntry } from '../types'
import DoctorAvatar from '../DoctorAvatar'
import DepartmentChip from '../DepartmentChip'
import { departmentIcon } from '../departmentIcon'
import { doctorSummaryLine } from '../format'
import { useStaggerReveal } from '../useStaggerReveal'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import { currentDayOfWeek, formatWorkingHours, isAvailableNow, isoDateToday, todaysScheduleEntries } from './doctorSchedule'
import AddDoctorModal from './AddDoctorModal'
import DoctorWorkspace from './DoctorWorkspace'
import { usePreviewPopover } from '../usePreviewPopover'

const ALL_FILTER_VALUE = '__all__'

interface DoctorRow {
  doctor: Doctor
  departments: Department[]
  todaysHours: string[]
  availableNow: boolean
  todaysAppointmentCount: number
  waitingCount: number
}

// Hover/focus preview popover, anchored to the doctor's name -- the name
// itself is always plain visible text in the row (never hidden behind
// this), so the popover is purely an enhancement for a faster look at
// specialization/departments/experience without opening the full
// workspace. Same pointerType-gated hover + focus trigger as
// DepartmentsPanel.tsx's DepartmentCard; the viewport-edge collision-
// avoidance and dismissal mechanics come from usePreviewPopover.ts,
// shared with that same card rather than a second copy.
function DoctorPreviewTrigger({ row, onView }: { row: DoctorRow; onView: () => void }) {
  const { open, setOpen, containerRef, popoverRef, shift, overflowsBottom } =
    usePreviewPopover<HTMLSpanElement>()

  const summaryLine = doctorSummaryLine(row.doctor)

  return (
    <span
      ref={containerRef}
      className="doctor-name-trigger"
      onPointerEnter={(e) => {
        if (e.pointerType === 'mouse') setOpen(true)
      }}
      onPointerLeave={(e) => {
        if (e.pointerType === 'mouse') setOpen(false)
      }}
    >
      <button
        type="button"
        className="doctor-name-button"
        onClick={() => setOpen((prev) => !prev)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-describedby={open ? `doctor-preview-${row.doctor.id}` : undefined}
      >
        <DoctorAvatar photoUrl={row.doctor.photo_url} name={row.doctor.name} size={40} />
        <span className="doctor-name-block">
          <span className="doctor-name-text">{row.doctor.name}</span>
          {row.doctor.specialization && <span className="muted doctor-name-specialization">{row.doctor.specialization}</span>}
        </span>
      </button>

      {open && (
        <div
          ref={popoverRef}
          id={`doctor-preview-${row.doctor.id}`}
          role="tooltip"
          className={`doctor-preview-popover${overflowsBottom ? ' above' : ''}`}
          style={{ '--popover-shift': `${shift}px` } as CSSProperties}
        >
          <p className="doctor-preview-name">{row.doctor.name}</p>
          {row.doctor.specialization && <p className="muted doctor-preview-line">{row.doctor.specialization}</p>}
          {summaryLine && <p className="muted doctor-preview-line">{summaryLine}</p>}
          {row.doctor.education_location && <p className="muted doctor-preview-line">{row.doctor.education_location}</p>}
          <p className="doctor-preview-departments-label">Departments</p>
          {row.departments.length > 0 ? (
            <div className="doctor-department-chips">
              {row.departments.map((d) => {
                const Icon = departmentIcon(d.name)
                return <DepartmentChip key={d.id} department={d} icon={<Icon size={12} weight="bold" />} />
              })}
            </div>
          ) : (
            <p className="muted doctor-preview-line">Not assigned to any department.</p>
          )}
          <button type="button" className="btn btn-sm doctor-preview-view" onClick={onView}>
            View doctor →
          </button>
        </div>
      )}
    </span>
  )
}

export default function DoctorsPanel({
  isAdmin,
  onGoToQueue,
}: {
  isAdmin: boolean
  // Overview's "View queue" quick action, and the directory's own Queue
  // shortcut, both hand off to the existing standalone Queue page
  // (QueuePanel.tsx) rather than duplicating QueueSection a third time.
  onGoToQueue?: (doctorId: number) => void
}) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [doctorDepartments, setDoctorDepartments] = useState<Map<number, Department[]>>(new Map())
  const [schedules, setSchedules] = useState<Map<number, DoctorScheduleEntry[]>>(new Map())
  const [blocksByDoctor, setBlocksByDoctor] = useState<Map<number, DoctorBlockEntry[]>>(new Map())
  const [todaysAppointments, setTodaysAppointments] = useState<AdminAppointment[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchText, setSearchText] = useState('')
  const [departmentFilter, setDepartmentFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedDoctor, setSelectedDoctor] = useState<Doctor | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const listRef = useStaggerReveal<HTMLDivElement>([doctors], '.doctor-row, .doctor-mobile-card')

  function load() {
    setLoading(true)
    setError(null)
    const today = isoDateToday()
    Promise.all([listAllDoctors(), listDepartments(), listAdminAppointments({ date_from: today, date_to: today })])
      .then(async ([allDoctors, departmentList, todaysAppts]) => {
        setDoctors(allDoctors)
        setDepartments(departmentList)
        setTodaysAppointments(todaysAppts.items)

        const [perDepartment, perDoctorSchedule, perDoctorBlocks] = await Promise.all([
          Promise.all(departmentList.map((d) => listDoctorsInDepartment(d.id).catch(() => [] as Doctor[]))),
          Promise.all(allDoctors.map((d) => getDoctorScheduleAdmin(d.id).catch(() => [] as DoctorScheduleEntry[]))),
          Promise.all(allDoctors.map((d) => getDoctorBlocks(d.id).catch(() => [] as DoctorBlockEntry[]))),
        ])

        const deptMap = new Map<number, Department[]>()
        departmentList.forEach((dept, i) => {
          for (const doc of perDepartment[i]) {
            deptMap.set(doc.id, [...(deptMap.get(doc.id) ?? []), dept])
          }
        })
        setDoctorDepartments(deptMap)

        const scheduleMap = new Map<number, DoctorScheduleEntry[]>()
        allDoctors.forEach((d, i) => scheduleMap.set(d.id, perDoctorSchedule[i]))
        setSchedules(scheduleMap)

        const blocksMap = new Map<number, DoctorBlockEntry[]>()
        allDoctors.forEach((d, i) => blocksMap.set(d.id, perDoctorBlocks[i]))
        setBlocksByDoctor(blocksMap)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctors'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  function backToDirectory() {
    setSelectedDoctor(null)
    load()
  }

  if (selectedDoctor) {
    return (
      <DoctorWorkspace
        doctor={selectedDoctor}
        isAdmin={isAdmin}
        onBack={backToDirectory}
        onGoToQueue={onGoToQueue}
      />
    )
  }

  const today = isoDateToday()
  const dayOfWeek = currentDayOfWeek()

  const rows: DoctorRow[] = doctors.map((doctor) => {
    const schedule = schedules.get(doctor.id) ?? []
    const blocks = blocksByDoctor.get(doctor.id) ?? []
    const doctorAppointmentsToday = todaysAppointments.filter((a) => a.doctor_id === doctor.id)
    const waiting = doctorAppointmentsToday.filter((a) => a.status === 'CHECKED_IN' && a.token_number !== null)
    return {
      doctor,
      departments: doctorDepartments.get(doctor.id) ?? [],
      todaysHours: formatWorkingHours(todaysScheduleEntries(schedule, today, dayOfWeek)),
      availableNow: isAvailableNow(schedule, blocks),
      todaysAppointmentCount: doctorAppointmentsToday.length,
      waitingCount: waiting.length,
    }
  })

  const availableNowCount = rows.filter((r) => r.availableNow).length

  const searchNeedle = searchText.trim().toLowerCase()
  const visibleRows = rows.filter((row) => {
    if (departmentFilter && !row.departments.some((d) => String(d.id) === departmentFilter)) return false
    if (statusFilter === 'available' && !row.availableNow) return false
    if (statusFilter === 'unavailable' && row.availableNow) return false
    if (!searchNeedle) return true
    return (
      row.doctor.name.toLowerCase().includes(searchNeedle) ||
      (row.doctor.specialization ?? '').toLowerCase().includes(searchNeedle)
    )
  })

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Doctors</h2>
          <p className="muted">Manage doctors, their specializations, schedules, and appointment settings.</p>
        </div>
        {isAdmin && (
          <button type="button" className="btn btn-sm" onClick={() => setAddOpen(true)}>
            + Add Doctor
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}

      <div className="dashboard-grid" style={{ marginBottom: 'var(--space-4)' }}>
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <Stethoscope size={22} weight="regular" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{doctors.length}</span>
            <span className="stat-label">Doctors</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <Buildings size={22} weight="regular" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{departments.length}</span>
            <span className="stat-label">Departments</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <CalendarCheck size={22} weight="regular" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{todaysAppointments.length}</span>
            <span className="stat-label">Appointments (Today)</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon" aria-hidden="true">
            <UsersThree size={22} weight="regular" />
          </span>
          <div className="stat-body">
            <span className="stat-value">{availableNowCount}</span>
            <span className="stat-label">Currently Available</span>
          </div>
        </div>
      </div>

      <div className="filter-bar">
        <div className="filter-bar-search-row">
          <div className="department-admin-search filter-bar-search">
            <MagnifyingGlass size={16} aria-hidden="true" />
            <input
              type="search"
              placeholder="Search doctors by name, specialization…"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />
          </div>
          <div className="filter-bar-fields">
            <label className="inline-label">
              Department
              <Select
                value={departmentFilter || ALL_FILTER_VALUE}
                onValueChange={(v) => setDepartmentFilter(v === ALL_FILTER_VALUE ? '' : v)}
              >
                <SelectTrigger className="filter-select-trigger">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_FILTER_VALUE}>All departments</SelectItem>
                  {departments.map((d) => (
                    <SelectItem key={d.id} value={String(d.id)}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="inline-label">
              Status
              <Select
                value={statusFilter || ALL_FILTER_VALUE}
                onValueChange={(v) => setStatusFilter(v === ALL_FILTER_VALUE ? '' : v)}
              >
                <SelectTrigger className="filter-select-trigger">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_FILTER_VALUE}>All status</SelectItem>
                  <SelectItem value="available">Available now</SelectItem>
                  <SelectItem value="unavailable">Not available now</SelectItem>
                </SelectContent>
              </Select>
            </label>
          </div>
        </div>
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading doctors…
        </div>
      )}
      {!loading && doctors.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <Stethoscope size={28} weight="light" />
          </span>
          No doctors yet.
        </div>
      )}
      {!loading && doctors.length > 0 && visibleRows.length === 0 && (
        <p className="muted">No doctors match your search or filters.</p>
      )}

      {!loading && visibleRows.length > 0 && (
        <div ref={listRef}>
          <div className="data-table-wrap doctor-directory-table-wrap">
            <table className="data-table doctor-directory-table">
              <thead>
                <tr>
                  <th>Doctor</th>
                  <th>Departments</th>
                  <th>Today&apos;s schedule</th>
                  <th>Today</th>
                  <th>Status</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => (
                  <tr key={row.doctor.id} className="doctor-row">
                    <td>
                      <DoctorPreviewTrigger row={row} onView={() => setSelectedDoctor(row.doctor)} />
                    </td>
                    <td>
                      {row.departments.length > 0 ? (
                        <span className="doctor-department-chips">
                          {row.departments.map((d) => {
                            const Icon = departmentIcon(d.name)
                            return <DepartmentChip key={d.id} department={d} icon={<Icon size={12} weight="bold" />} />
                          })}
                        </span>
                      ) : (
                        <span className="muted">Unassigned</span>
                      )}
                    </td>
                    <td>
                      {row.todaysHours.length > 0 ? (
                        row.todaysHours.map((h) => <div key={h}>{h}</div>)
                      ) : (
                        <span className="muted">Not working today</span>
                      )}
                    </td>
                    <td>
                      {row.todaysAppointmentCount} {row.todaysAppointmentCount === 1 ? 'appointment' : 'appointments'}
                      {row.waitingCount > 0 && <div className="muted">{row.waitingCount} waiting</div>}
                    </td>
                    <td>
                      <span className={row.availableNow ? 'pill status-confirmed' : 'pill status-pending'}>
                        {row.availableNow ? 'Available' : 'Unavailable'}
                      </span>
                    </td>
                    <td>
                      <button type="button" className="btn-secondary btn btn-sm" onClick={() => setSelectedDoctor(row.doctor)}>
                        View doctor →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <ul className="doctor-mobile-list">
            {visibleRows.map((row) => (
              <li key={row.doctor.id} className="doctor-mobile-card" onClick={() => setSelectedDoctor(row.doctor)}>
                <div className="doctor-mobile-card-top">
                  <DoctorAvatar photoUrl={row.doctor.photo_url} name={row.doctor.name} size={40} />
                  <span>
                    <span className="doctor-name-text">{row.doctor.name}</span>
                    {row.doctor.specialization && <span className="muted doctor-name-specialization">{row.doctor.specialization}</span>}
                  </span>
                  <span className={row.availableNow ? 'pill status-confirmed' : 'pill status-pending'}>
                    {row.availableNow ? 'Available' : 'Unavailable'}
                  </span>
                </div>
                {row.departments.length > 0 && (
                  <span className="doctor-department-chips">
                    {row.departments.map((d) => {
                      const Icon = departmentIcon(d.name)
                      return <DepartmentChip key={d.id} department={d} icon={<Icon size={12} weight="bold" />} />
                    })}
                  </span>
                )}
                <div className="muted">
                  {row.todaysHours.length > 0 ? row.todaysHours.join(', ') : 'Not working today'}
                </div>
                <div className="muted">
                  {row.todaysAppointmentCount} {row.todaysAppointmentCount === 1 ? 'appointment' : 'appointments'} today
                  {row.waitingCount > 0 ? ` · ${row.waitingCount} waiting` : ''}
                </div>
                <button type="button" className="btn-secondary btn btn-sm" onClick={() => setSelectedDoctor(row.doctor)}>
                  View doctor →
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {addOpen && (
        <AddDoctorModal
          departments={departments}
          onClose={() => setAddOpen(false)}
          onCreated={(created) => {
            load()
            setSelectedDoctor(created)
          }}
        />
      )}
    </section>
  )
}
