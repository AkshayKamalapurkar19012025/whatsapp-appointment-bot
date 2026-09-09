import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import {
  ApiError,
  assignDoctorToDepartment,
  listAllDoctors,
  listDoctorsInDepartment,
  removeDoctorFromDepartment,
} from '../api'
import type { Department, Doctor } from '../types'

// The reverse direction of DoctorDetail.tsx's own DepartmentAssignment
// (that one picks departments for a single doctor; this picks doctors
// for a single department) -- same two existing endpoints
// (assignDoctorToDepartment/removeDoctorFromDepartment), no new API.
export default function ManageDepartmentDoctorsModal({
  department,
  onClose,
  onChanged,
}: {
  department: Department
  onClose: () => void
  // Refreshes the parent grid's own doctor counts/popover data --
  // this modal keeps its own local checklist state, so the parent
  // doesn't reflect a change until it reloads.
  onChanged: () => void
}) {
  const [allDoctors, setAllDoctors] = useState<Doctor[]>([])
  const [assignedIds, setAssignedIds] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([listAllDoctors(), listDoctorsInDepartment(department.id)])
      .then(([all, assigned]) => {
        setAllDoctors(all)
        setAssignedIds(new Set(assigned.map((d) => d.id)))
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctors'))
      .finally(() => setLoading(false))
  }, [department.id])

  async function toggle(doctorId: number, currentlyAssigned: boolean) {
    setError(null)
    setBusyId(doctorId)
    try {
      if (currentlyAssigned) {
        await removeDoctorFromDepartment(doctorId, department.id)
        setAssignedIds((prev) => {
          const next = new Set(prev)
          next.delete(doctorId)
          return next
        })
      } else {
        await assignDoctorToDepartment(doctorId, department.id)
        setAssignedIds((prev) => new Set(prev).add(doctorId))
      }
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update this doctor’s assignment')
    } finally {
      setBusyId(null)
    }
  }

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`Manage doctors in ${department.name}`}
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">Doctors in {department.name}</h3>
        <p className="muted" style={{ marginTop: 0, marginBottom: 'var(--space-4)' }}>
          Check a doctor to add them to this department, uncheck to remove.
        </p>

        {error && <p className="error">{error}</p>}

        {loading && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading…
          </div>
        )}

        {!loading && allDoctors.length === 0 && <p className="muted">No doctors yet.</p>}

        {!loading && allDoctors.length > 0 && (
          <div className="manage-doctors-list">
            {allDoctors.map((doctor) => {
              const assigned = assignedIds.has(doctor.id)
              return (
                <div key={doctor.id} className="manage-doctors-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={assigned}
                      disabled={busyId === doctor.id}
                      onChange={() => toggle(doctor.id, assigned)}
                    />
                    <span>
                      <strong>{doctor.name}</strong>
                      {(doctor.qualifications || doctor.specialization) && (
                        <span className="muted" style={{ display: 'block', fontSize: '0.82rem' }}>
                          {[doctor.qualifications, doctor.specialization].filter(Boolean).join(' · ')}
                        </span>
                      )}
                    </span>
                  </label>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
