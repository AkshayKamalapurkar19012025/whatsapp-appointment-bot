import { useEffect, useState } from 'react'
import { ApiError, getDoctorBlocks, getDoctorDepartments, getDoctorScheduleAdmin, updateDoctorSlotSettings } from '../api'
import type { Department, Doctor, DoctorBlockEntry, DoctorScheduleEntry } from '../types'
import ScheduleMonthView from './ScheduleMonthView'
import ConfigureScheduleModal from './ConfigureScheduleModal'
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
  const [error, setError] = useState<string | null>(null)

  const [defaultDuration, setDefaultDuration] = useState(doctor.default_duration_minutes)
  const [bufferMinutes, setBufferMinutes] = useState(doctor.buffer_minutes)

  // Which date the Configure Schedule popup is open for, and its
  // pre-fill (the date's existing schedule, if any -- empty means
  // create-from-scratch). null = popup closed.
  const [configureDate, setConfigureDate] = useState<string | null>(null)
  const [configureEditingGroup, setConfigureEditingGroup] = useState<ScheduleBlock[]>([])

  const blocks = mergeEntriesIntoBlocks(entries, DEFAULT_MERGE_GAP_MINUTES)

  function load() {
    Promise.all([getDoctorScheduleAdmin(doctor.id), getDoctorDepartments(doctor.id), getDoctorBlocks(doctor.id)])
      .then(([schedule, depts, blocksResult]) => {
        setEntries(schedule)
        setDepartments(depts)
        setOneOffBlocks(blocksResult)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load schedule'))
  }
  useEffect(load, [doctor.id])

  async function persistSlotSettings(nextDuration: number, nextBuffer: number) {
    try {
      await updateDoctorSlotSettings(doctor.id, {
        default_duration_minutes: nextDuration,
        buffer_minutes: nextBuffer,
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save slot settings')
    }
  }

  function handleChangeDuration(minutes: number) {
    setDefaultDuration(minutes)
    persistSlotSettings(minutes, bufferMinutes)
  }

  function handleChangeBuffer(minutes: number) {
    setBufferMinutes(minutes)
    persistSlotSettings(defaultDuration, minutes)
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

  return (
    <div>
      {error && <p className="error">{error}</p>}

      <ScheduleMonthView
        blocks={blocks}
        oneOffBlocks={oneOffBlocks}
        departments={departments}
        defaultDuration={defaultDuration}
        bufferMinutes={bufferMinutes}
        isAdmin={isAdmin}
        onDateClick={openConfigureFor}
        onAddSchedule={openAddSchedule}
        onChangeDuration={handleChangeDuration}
        onChangeBuffer={handleChangeBuffer}
      />

      {configureDate && (
        <ConfigureScheduleModal
          doctorId={doctor.id}
          departments={departments}
          defaultDuration={defaultDuration}
          bufferMinutes={bufferMinutes}
          initialDate={configureDate}
          editingGroup={configureEditingGroup}
          onClose={closeConfigure}
          onSaved={load}
        />
      )}
    </div>
  )
}
