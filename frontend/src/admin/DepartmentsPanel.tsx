import { useEffect, useMemo, useState } from 'react'
import {
  Buildings,
  Check,
  DotsThreeVertical,
  MagnifyingGlass,
  PencilSimple,
  Trash,
  UsersThree,
  X,
} from '@phosphor-icons/react'
import {
  ApiError,
  deleteDepartment,
  listAllDoctors,
  listDepartments,
  listDoctorsInDepartment,
  updateDepartment,
} from '../api'
import type { Department, Doctor } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'
import { accentClassFor } from '../cardAccent'
import { departmentIcon } from '../departmentIcon'
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
import AddDepartmentModal from './AddDepartmentModal'
import ManageDepartmentDoctorsModal from './ManageDepartmentDoctorsModal'

// Doctor-list preview, shown on hover (desktop) or tap (mobile) of a
// card's doctor-count line -- onMouseEnter/onMouseLeave for hover,
// onClick as the tap-to-toggle fallback (a touch device fires both;
// toggling on click covers it without needing a separate touch path),
// same convention MonthGrid.tsx's own day-popover already uses for
// this exact hover-vs-tap split.
function DoctorListPreview({ department, count }: { department: Department; count: number }) {
  const [open, setOpen] = useState(false)
  // The visible count always comes from the `count` prop (the parent's
  // already-fetched doctorCounts, loaded eagerly for every card) --
  // this component's own lazy `doctors` fetch is only for the popover's
  // detailed per-doctor rows, which nobody needs until they actually
  // hover/tap, so it stays null until then.
  const [doctors, setDoctors] = useState<Doctor[] | null>(null)

  function load() {
    if (doctors !== null) return
    listDoctorsInDepartment(department.id)
      .then(setDoctors)
      .catch(() => setDoctors([]))
  }

  return (
    <span
      className="department-admin-card-count-wrap"
      style={{ position: 'relative' }}
      onMouseEnter={() => {
        load()
        setOpen(true)
      }}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="link"
        style={{ font: 'inherit', color: 'inherit', padding: 0 }}
        onClick={(e) => {
          e.stopPropagation()
          load()
          setOpen((v) => !v)
        }}
        disabled={count === 0}
      >
        <UsersThree size={14} weight="bold" aria-hidden="true" /> {count} {count === 1 ? 'doctor' : 'doctors'}
      </button>

      {open && doctors && doctors.length > 0 && (
        <div className="department-admin-popover" onClick={(e) => e.stopPropagation()}>
          <p className="department-admin-popover-title">Doctors in {department.name}</p>
          {doctors.map((d) => (
            <div key={d.id} className="department-admin-popover-row">
              <span
                className={`department-card-icon ${accentClassFor(d.name)}`}
                aria-hidden="true"
                style={{ width: 28, height: 28 }}
              >
                <UsersThree size={14} />
              </span>
              <span>
                <span className="department-admin-popover-doctor-name">{d.name}</span>
                {(d.qualifications || d.specialization) && (
                  <span className="department-admin-popover-doctor-meta">
                    {[d.qualifications, d.specialization].filter(Boolean).join(' · ')}
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </span>
  )
}

export default function DepartmentsPanel({ isAdmin }: { isAdmin: boolean }) {
  const [departments, setDepartments] = useState<Department[]>([])
  // Doctor belongs to a department via a many-to-many join
  // (doctor_departments), not a column on either row, so there's no
  // count to read straight off Department -- fetched per-department
  // the same way DoctorsPanel.tsx already builds its own groups,
  // rather than adding a new backend aggregate for a handful of rows.
  const [doctorCounts, setDoctorCounts] = useState<Record<number, number>>({})
  const [totalDoctors, setTotalDoctors] = useState(0)
  const [searchText, setSearchText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState('')
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Department | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [manageTarget, setManageTarget] = useState<Department | null>(null)
  const gridRef = useStaggerReveal<HTMLDivElement>([departments])

  function load() {
    setLoading(true)
    listDepartments()
      .then(async (list) => {
        setDepartments(list)
        const counts = await Promise.all(
          list.map((d) => listDoctorsInDepartment(d.id).then((doctors) => doctors.length).catch(() => 0)),
        )
        setDoctorCounts(Object.fromEntries(list.map((d, i) => [d.id, counts[i]])))
        // Total is the count of distinct doctors across all departments,
        // not the sum of per-department counts (a doctor in two
        // departments shouldn't be counted twice) -- listAllDoctors()
        // is the source of truth for "how many doctors exist" already
        // used elsewhere (DashboardPanel, the Doctors nav section).
        listAllDoctors().then((all) => setTotalDoctors(all.length)).catch(() => undefined)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load departments'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const searchNeedle = searchText.trim().toLowerCase()
  const visibleDepartments = useMemo(
    () => departments.filter((d) => !searchNeedle || d.name.toLowerCase().includes(searchNeedle)),
    [departments, searchNeedle],
  )

  function startEdit(d: Department) {
    setError(null)
    setEditingId(d.id)
    setEditingName(d.name)
  }

  function cancelEdit() {
    setEditingId(null)
    setEditingName('')
  }

  async function handleRename(e: React.FormEvent, departmentId: number) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await updateDepartment(departmentId, editingName)
      cancelEdit()
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update department')
    } finally {
      setBusy(false)
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return
    setError(null)
    setDeletingId(deleteTarget.id)
    try {
      await deleteDepartment(deleteTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove department')
    } finally {
      setDeletingId(null)
      setDeleteTarget(null)
    }
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Departments</h2>
          <p className="muted">Manage departments and their assigned doctors.</p>
          <p className="department-admin-header-line muted">
            {departments.length} {departments.length === 1 ? 'department' : 'departments'} ·{' '}
            {totalDoctors} {totalDoctors === 1 ? 'doctor' : 'doctors'}
          </p>
        </div>
        {isAdmin && (
          <button type="button" className="btn btn-sm" onClick={() => setShowAddModal(true)}>
            + Add Department
          </button>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      <div className="department-admin-search">
        <MagnifyingGlass size={16} aria-hidden="true" />
        <input
          type="search"
          placeholder="Search departments…"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
        />
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading departments…
        </div>
      )}
      {!loading && departments.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <Buildings size={28} weight="light" />
          </span>
          No departments yet.
        </div>
      )}
      {!loading && departments.length > 0 && visibleDepartments.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <MagnifyingGlass size={28} weight="light" />
          </span>
          No departments match your search.
        </div>
      )}

      {!loading && visibleDepartments.length > 0 && (
        <div className="department-admin-grid" ref={gridRef}>
          {visibleDepartments.map((d) =>
            editingId === d.id ? (
              <form
                key={d.id}
                className="department-admin-card"
                style={{ justifyContent: 'center' }}
                onSubmit={(e) => handleRename(e, d.id)}
              >
                <input autoFocus value={editingName} onChange={(e) => setEditingName(e.target.value)} required />
                <div className="card-grid-item-actions">
                  <button type="submit" className="icon-btn" disabled={busy} aria-label="Save">
                    <Check size={16} />
                  </button>
                  <button type="button" className="icon-btn" onClick={cancelEdit} aria-label="Cancel">
                    <X size={16} />
                  </button>
                </div>
              </form>
            ) : (
              <div key={d.id} className="department-admin-card">
                <div className="department-admin-card-top">
                  <span className={`department-card-icon ${accentClassFor(d.name)}`} aria-hidden="true">
                    {(() => {
                      const Icon = departmentIcon(d.name)
                      return <Icon size={20} weight="duotone" />
                    })()}
                  </span>
                  {isAdmin && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button type="button" className="overflow-menu-trigger" aria-label={`Actions for ${d.name}`}>
                          <DotsThreeVertical size={18} weight="bold" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => startEdit(d)}>
                          <PencilSimple size={15} /> Edit department
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => setManageTarget(d)}>
                          <UsersThree size={15} /> Manage doctors
                        </DropdownMenuItem>
                        <DropdownMenuItem variant="danger" onSelect={() => setDeleteTarget(d)}>
                          <Trash size={15} /> Delete department
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </div>

                <span className="department-admin-card-name">{d.name}</span>

                <span className={`department-admin-card-count${(doctorCounts[d.id] ?? 0) === 0 ? ' zero' : ''}`}>
                  <DoctorListPreview department={d} count={doctorCounts[d.id] ?? 0} />
                </span>
              </div>
            ),
          )}
        </div>
      )}

      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this department?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget &&
                `"${deleteTarget.name}" will no longer appear for booking. Doctors assigned to it keep their history.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmDelete} disabled={deletingId === deleteTarget?.id}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {showAddModal && (
        <AddDepartmentModal onClose={() => setShowAddModal(false)} onCreated={load} />
      )}

      {manageTarget && (
        <ManageDepartmentDoctorsModal
          department={manageTarget}
          onClose={() => setManageTarget(null)}
          onChanged={load}
        />
      )}
    </section>
  )
}
