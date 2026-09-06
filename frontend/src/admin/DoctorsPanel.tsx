import { useEffect, useId, useState } from 'react'
import { Stethoscope } from '@phosphor-icons/react'
import { ApiError, createDoctor, listAllDoctors, listDepartments, listDoctorsInDepartment } from '../api'
import type { Department, Doctor } from '../types'
import DoctorAvatar from '../DoctorAvatar'
import { doctorSummaryLine, formatDateTime } from '../format'
import { useStaggerReveal } from '../useStaggerReveal'
import DoctorDetail from './DoctorDetail'

interface DoctorGroup {
  key: string
  label: string
  doctors: Doctor[]
}

export default function DoctorsPanel({ isAdmin }: { isAdmin: boolean }) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [groups, setGroups] = useState<DoctorGroup[]>([])
  const [name, setName] = useState('')
  const [specialization, setSpecialization] = useState('')
  const [subSpecialization, setSubSpecialization] = useState('')
  const [qualifications, setQualifications] = useState('')
  const [yearsOfExperience, setYearsOfExperience] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [selectedDoctor, setSelectedDoctor] = useState<Doctor | null>(null)
  const listRef = useStaggerReveal<HTMLDivElement>([groups], '.doctor-card')
  const specializationListId = useId()

  // Doctors have no department field of their own (see types.ts) -- the
  // relationship is doctor<->department, many-to-many, so grouping for
  // display means fetching each department's own doctor list and
  // bucketing by that rather than reading a column off Doctor itself.
  // Any doctor absent from every department's list falls into its own
  // "Unassigned" bucket so it isn't silently dropped from the grid.
  function load() {
    setLoading(true)
    setError(null)
    Promise.all([listAllDoctors(), listDepartments()])
      .then(async ([allDoctors, departmentList]) => {
        setDoctors(allDoctors)
        setDepartments(departmentList)
        const perDepartment = await Promise.all(
          departmentList.map((d) => listDoctorsInDepartment(d.id).catch(() => [] as Doctor[])),
        )
        const assignedIds = new Set<number>()
        const builtGroups: DoctorGroup[] = departmentList
          .map((d: Department, i: number) => {
            for (const doc of perDepartment[i]) assignedIds.add(doc.id)
            return { key: `dept-${d.id}`, label: d.name, doctors: perDepartment[i] }
          })
          .filter((g) => g.doctors.length > 0)
        const unassigned = allDoctors.filter((d) => !assignedIds.has(d.id))
        if (unassigned.length > 0) {
          builtGroups.push({ key: 'unassigned', label: 'Unassigned', doctors: unassigned })
        }
        setGroups(builtGroups)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load doctors'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const created = await createDoctor({
        name,
        specialization,
        sub_specialization: subSpecialization || undefined,
        qualifications: qualifications || undefined,
        years_of_experience: yearsOfExperience ? Number(yearsOfExperience) : undefined,
      })
      setName('')
      setSpecialization('')
      setSubSpecialization('')
      setQualifications('')
      setYearsOfExperience('')
      load()
      setSelectedDoctor(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create doctor')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Doctors</h2>
      {error && <p className="error">{error}</p>}

      {isAdmin && (
        <form className="doctor-form-grid" onSubmit={handleCreate}>
          <label className="inline-label doctor-form-full">
            Name
            <input
              placeholder="Dr. Jane Doe"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
          <label className="inline-label">
            Specialization
            <input
              placeholder="Cardiology"
              list={specializationListId}
              value={specialization}
              onChange={(e) => setSpecialization(e.target.value)}
              required
            />
            {/* Suggestions only, not a hard constraint -- specialization
                is the doctor's own medical field, which doesn't always
                equal one of the clinic's administrative departments, so
                free text stays possible. See this form's own PR for the
                reasoning. */}
            <datalist id={specializationListId}>
              {departments.map((d) => (
                <option key={d.id} value={d.name} />
              ))}
            </datalist>
          </label>
          <label className="inline-label">
            Sub-specialization <span className="muted">(optional)</span>
            <input
              placeholder="Interventional Cardiology"
              value={subSpecialization}
              onChange={(e) => setSubSpecialization(e.target.value)}
            />
          </label>
          <label className="inline-label">
            Qualifications <span className="muted">(optional)</span>
            <input
              placeholder="MBBS, MD (Cardiology)"
              value={qualifications}
              onChange={(e) => setQualifications(e.target.value)}
            />
          </label>
          <label className="inline-label">
            Years of experience <span className="muted">(optional)</span>
            <input
              type="number"
              min={0}
              max={80}
              placeholder="10"
              value={yearsOfExperience}
              onChange={(e) => setYearsOfExperience(e.target.value)}
            />
          </label>
          <div className="doctor-form-actions">
            <button type="submit" className="btn-sm" disabled={busy}>
              {busy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      )}

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

      {!loading && groups.length > 0 && (
        <div ref={listRef}>
          {groups.map((group) => (
            <div key={group.key} className="doctor-group">
              <h4 className="doctor-group-label">{group.label}</h4>
              <div className="doctor-grid">
                {group.doctors.map((d) => {
                  const summaryLine = doctorSummaryLine(d)
                  return (
                    <button
                      key={d.id}
                      type="button"
                      className={selectedDoctor?.id === d.id ? 'doctor-card selected' : 'doctor-card'}
                      onClick={() => setSelectedDoctor(d)}
                    >
                      <span className="doctor-card-header">
                        <DoctorAvatar photoUrl={d.photo_url} name={d.name} size={44} />
                        <span>
                          <span>{d.name}</span>
                          {d.specialization && <span className="option-subtitle">{d.specialization}</span>}
                        </span>
                      </span>
                      {/* Deliberately just these two lines -- the complete
                          education history and every other profile detail
                          live one click away in the Profile tab, not here
                          (see this card's own PR for why: an admin listing
                          this dense already needs to stay scannable). */}
                      {(summaryLine || d.education_location) && (
                        <span className="doctor-card-summary">
                          {summaryLine && <span className="muted doctor-card-meta">{summaryLine}</span>}
                          {d.education_location && (
                            <span className="muted doctor-card-meta">{d.education_location}</span>
                          )}
                        </span>
                      )}
                      <span className="doctor-card-footer">
                        <span className="muted doctor-added-meta">
                          Added {formatDateTime(d.created_at)}
                          {d.created_by ? ` by ${d.created_by}` : ''}
                        </span>
                        <span className="doctor-card-view-profile">{isAdmin ? 'Edit' : 'View Profile'}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedDoctor && <DoctorDetail doctor={selectedDoctor} isAdmin={isAdmin} />}
    </section>
  )
}
