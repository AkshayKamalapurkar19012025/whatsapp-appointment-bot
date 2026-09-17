import { useEffect, useState } from 'react'
import {
  ApiError,
  getDoctorBlocks,
  getDoctorDepartments,
  getDoctorScheduleAdmin,
  listAppointmentTypesForDoctor,
  updateDoctorSlotSettings,
} from '../api'
import type { AppointmentType, Department, Doctor, DoctorBlockEntry, DoctorScheduleEntry } from '../types'
import ScheduleMonthView from './ScheduleMonthView'
import ConfigureScheduleModal from './ConfigureScheduleModal'
import DuplicateScheduleModal from './DuplicateScheduleModal'
import RemoveScheduleModal from './RemoveScheduleModal'
import { DEFAULT_MERGE_GAP_MINUTES, editGroupForDate, mergeEntriesIntoBlocks, type ScheduleBlock } from './doctorSchedule'

// The Schedule tab -- a calendar WORKSPACE, not a configuration form.
// This component is a thin container: it fetches the doctor's schedule/
// departments/time-off, holds the doctor-level slot settings (persisted
// immediately, no draft), and decides what the Configure Schedule popup
// opens into. Every actual create/edit/remove happens inside that popup
// (ConfigureScheduleModal.tsx), which persists directly to the backend
// and calls back into `load()` to refresh -- there is no page-wide
// draft/dirty state or Save/Cancel bar here anymore; the calendar
// (ScheduleMonthView.tsx) is a pure view of whatever's actually saved.
//
// Workflow: Monthly Calendar -> click a date (or "+ Add Schedule") ->
// Configure Schedule popup (Scope -> Working Hours/Breaks/Department ->
// Generated Slots Preview -> Review & Save) -> popup closes -> calendar
// re-fetches and updates immediately.
export default function ScheduleGrid({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [entries, setEntries] = useState<DoctorScheduleEntry[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  // One-off blocks (Time off tab) -- read-only context for the calendar's
  // "Time off" status, never edited from here.
  const [oneOffBlocks, setOneOffBlocks] = useState<DoctorBlockEntry[]>([])
  const [appointmentTypes, setAppointmentTypes] = useState<AppointmentType[]>([])
  const [error, setError] = useState<string | null>(null)

  const [bufferMinutes, setBufferMinutes] = useState(doctor.buffer_minutes)

  // Every "Working-Hours Preview" (ScheduleMonthView's calendar cells,
  // Configure/Duplicate Schedule's preview grids) needs SOME duration to
  // chop working hours into slot-sized chunks. This used to be
  // doctor.default_duration_minutes -- an admin-editable value with no
  // connection to any real appointment type, so the preview could (and
  // did) show slot boundaries that didn't match any actually-bookable
  // start time (see the "6:15 PM" confusion this replaced). Now it's
  // always a real appointment type's own duration_minutes -- the first
  // one in the doctor's assigned list (alphabetical, per
  // GET /doctors/{id}/appointment-types) -- so every boundary the
  // preview shows is one a patient could actually book, for at least
  // that appointment type. Falls back to doctor.default_duration_minutes
  // (still stored, no longer user-editable) only when no appointment
  // type is assigned yet, so the calendar still renders something
  // before that setup step is done.
  const previewDurationMinutes = appointmentTypes[0]?.duration_minutes ?? doctor.default_duration_minutes
  const previewDurationLabel = appointmentTypes[0] ? `${appointmentTypes[0].duration_minutes} minutes (${appointmentTypes[0].name})` : null

  // Which date the Configure Schedule popup is open for, and its
  // pre-fill (the date's existing schedule, if any -- empty means
  // create-from-scratch). null = popup closed.
  const [configureDate, setConfigureDate] = useState<string | null>(null)
  const [configureEditingGroup, setConfigureEditingGroup] = useState<ScheduleBlock[]>([])

  // Duplicate Schedule -- opened from Configure Schedule's edit-mode
  // "Schedule actions" menu, never from the calendar directly. The two
  // popups are mutually exclusive (Duplicate closes Configure first),
  // so there's no need to track more than one source at a time.
  const [duplicateSource, setDuplicateSource] = useState<{ date: string; group: ScheduleBlock[] } | null>(null)

  // Remove Schedule -- same pattern as Duplicate: opened from Configure
  // Schedule's edit-mode "Schedule actions" menu or its own footer
  // Remove Schedule buttons, mutually exclusive with Configure itself.
  const [removeSource, setRemoveSource] = useState<{ date: string; group: ScheduleBlock[] } | null>(null)

  const blocks = mergeEntriesIntoBlocks(entries, DEFAULT_MERGE_GAP_MINUTES)

  function load() {
    Promise.all([
      getDoctorScheduleAdmin(doctor.id),
      getDoctorDepartments(doctor.id),
      getDoctorBlocks(doctor.id),
      listAppointmentTypesForDoctor(doctor.id),
    ])
      .then(([schedule, depts, blocksResult, types]) => {
        setEntries(schedule)
        setDepartments(depts)
        setOneOffBlocks(blocksResult)
        setAppointmentTypes(types)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load schedule'))
  }
  useEffect(load, [doctor.id])

  // default_duration_minutes is still a required field on this endpoint
  // (see app/api/doctors.py's DoctorSlotSettingsUpdate) even though
  // nothing in the UI edits it anymore -- kept in sync with
  // previewDurationMinutes on every save so the stored value never goes
  // stale relative to whichever appointment type is currently first.
  async function handleChangeBuffer(minutes: number) {
    setBufferMinutes(minutes)
    try {
      await updateDoctorSlotSettings(doctor.id, {
        default_duration_minutes: previewDurationMinutes,
        buffer_minutes: minutes,
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save slot settings')
    }
  }

  function openConfigureFor(dateStr: string) {
    setConfigureDate(dateStr)
    setConfigureEditingGroup(editGroupForDate(blocks, dateStr))
  }

  function openAddSchedule() {
    const today = new Date()
    const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
    setConfigureDate(todayStr)
    setConfigureEditingGroup([])
  }

  function closeConfigure() {
    setConfigureDate(null)
    setConfigureEditingGroup([])
  }

  function openDuplicate() {
    if (!configureDate) return
    setDuplicateSource({ date: configureDate, group: configureEditingGroup })
    closeConfigure()
  }

  function closeDuplicate() {
    setDuplicateSource(null)
  }

  function openRemove() {
    if (!configureDate) return
    setRemoveSource({ date: configureDate, group: configureEditingGroup })
    closeConfigure()
  }

  function closeRemove() {
    setRemoveSource(null)
  }

  return (
    <div>
      {error && <p className="error">{error}</p>}

      <ScheduleMonthView
        blocks={blocks}
        oneOffBlocks={oneOffBlocks}
        departments={departments}
        previewDurationMinutes={previewDurationMinutes}
        previewDurationLabel={previewDurationLabel}
        bufferMinutes={bufferMinutes}
        isAdmin={isAdmin}
        onDateClick={openConfigureFor}
        onAddSchedule={openAddSchedule}
        onChangeBuffer={handleChangeBuffer}
      />

      {configureDate && (
        <ConfigureScheduleModal
          doctorId={doctor.id}
          departments={departments}
          blocks={blocks}
          defaultDuration={previewDurationMinutes}
          previewDurationLabel={previewDurationLabel}
          bufferMinutes={bufferMinutes}
          initialDate={configureDate}
          editingGroup={configureEditingGroup}
          onClose={closeConfigure}
          onSaved={load}
          onChangeBuffer={handleChangeBuffer}
          onDuplicate={openDuplicate}
          onRemove={openRemove}
        />
      )}

      {duplicateSource && (
        <DuplicateScheduleModal
          doctorId={doctor.id}
          departments={departments}
          sourceGroup={duplicateSource.group}
          sourceDate={duplicateSource.date}
          blocks={blocks}
          defaultDuration={previewDurationMinutes}
          bufferMinutes={bufferMinutes}
          onClose={closeDuplicate}
          onSaved={load}
        />
      )}

      {removeSource && (
        <RemoveScheduleModal
          doctorId={doctor.id}
          departments={departments}
          editingGroup={removeSource.group}
          initialDate={removeSource.date}
          onClose={closeRemove}
          onSaved={load}
        />
      )}
    </div>
  )
}
