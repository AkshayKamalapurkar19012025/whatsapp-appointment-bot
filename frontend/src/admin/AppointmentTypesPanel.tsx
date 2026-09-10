import { useEffect, useState } from 'react'
import { DotsThreeVertical, MagnifyingGlass, Tag, X } from '@phosphor-icons/react'
import { createPortal } from 'react-dom'
import {
  ApiError,
  createAppointmentType,
  getAppointmentTypeDetail,
  listAppointmentTypesAdmin,
  setAppointmentTypeActive,
  updateAppointmentType,
} from '../api'
import type { AppointmentTypeAdminRow, AppointmentTypeDetail } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../components/ui/dropdown-menu'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'

type StatusFilter = 'all' | 'active' | 'inactive'

// Appointment Types as OPD master data: the type/purpose of an OPD
// visit (Consultation, Follow-up, ...), NOT where duration or fee
// live -- those stay exactly where the existing architecture already
// puts them, per doctor, in doctor_appointment_types (see
// AppointmentTypeFormModal's own note, and the View drawer below,
// which reads them from there rather than duplicating them here).
export default function AppointmentTypesPanel({ isAdmin }: { isAdmin: boolean }) {
  const [types, setTypes] = useState<AppointmentTypeAdminRow[]>([])
  const [searchText, setSearchText] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [formTarget, setFormTarget] = useState<'add' | AppointmentTypeAdminRow | null>(null)
  const [viewTargetId, setViewTargetId] = useState<number | null>(null)
  const [deactivateTarget, setDeactivateTarget] = useState<AppointmentTypeAdminRow | null>(null)
  const [statusBusyId, setStatusBusyId] = useState<number | null>(null)

  const gridRef = useStaggerReveal<HTMLTableSectionElement>([types])

  function load() {
    setLoading(true)
    listAppointmentTypesAdmin()
      .then(setTypes)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointment types'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const searchNeedle = searchText.trim().toLowerCase()
  const visibleTypes = types.filter((t) => {
    if (statusFilter === 'active' && !t.active) return false
    if (statusFilter === 'inactive' && t.active) return false
    if (searchNeedle && !t.name.toLowerCase().includes(searchNeedle)) return false
    return true
  })

  function handleSaved(saved: AppointmentTypeAdminRow) {
    setTypes((prev) => {
      const exists = prev.some((t) => t.id === saved.id)
      if (exists) return prev.map((t) => (t.id === saved.id ? { ...t, ...saved } : t))
      return [...prev, { ...saved, doctor_count: 0 }]
    })
  }

  async function toggleActive(t: AppointmentTypeAdminRow, active: boolean) {
    setError(null)
    setStatusBusyId(t.id)
    try {
      const result = await setAppointmentTypeActive(t.id, active)
      setTypes((prev) => prev.map((x) => (x.id === t.id ? { ...x, active: result.active } : x)))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not ${active ? 'activate' : 'deactivate'} this appointment type`)
    } finally {
      setStatusBusyId(null)
      setDeactivateTarget(null)
    }
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Appointment Types</h2>
          <p className="muted">
            Manage the types of OPD visits offered by your clinic. Duration and fees are configured for each doctor.
          </p>
        </div>
        {isAdmin && (
          <button type="button" className="btn btn-sm" onClick={() => setFormTarget('add')}>
            + Add Appointment Type
          </button>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      <div className="appointment-types-toolbar">
        <label className="filter-bar-search-input appointment-types-search-input">
          <MagnifyingGlass size={16} aria-hidden="true" />
          <input
            type="search"
            placeholder="Search appointment types…"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            aria-label="Search appointment types"
          />
        </label>
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as StatusFilter)}>
          <SelectTrigger className="filter-select-trigger appointment-types-status-trigger" aria-label="Filter by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All status</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="inactive">Inactive</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading appointment types…
        </div>
      )}
      {!loading && visibleTypes.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <Tag size={28} weight="light" />
          </span>
          {types.length === 0 ? 'No appointment types yet.' : 'No appointment types match your search.'}
        </div>
      )}

      {!loading && visibleTypes.length > 0 && (
        <div className="admin-table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Doctors</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody ref={gridRef}>
              {visibleTypes.map((t) => (
                <tr key={t.id}>
                  <td>
                    <strong>{t.name}</strong>
                  </td>
                  <td>
                    {t.doctor_count} doctor{t.doctor_count === 1 ? '' : 's'}
                  </td>
                  <td>
                    <span className={`pill status-${t.active ? 'active' : 'inactive'}`}>
                      {t.active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td>
                    <div className="appointment-type-actions">
                      <button type="button" className="btn-secondary btn btn-sm" onClick={() => setViewTargetId(t.id)}>
                        View
                      </button>
                      {isAdmin && (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <button type="button" className="overflow-menu-trigger" aria-label={`More actions for ${t.name}`}>
                              <DotsThreeVertical size={18} weight="bold" />
                            </button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onSelect={() => setFormTarget(t)}>Edit</DropdownMenuItem>
                            {t.active ? (
                              <DropdownMenuItem variant="danger" onSelect={() => setDeactivateTarget(t)}>
                                Deactivate
                              </DropdownMenuItem>
                            ) : (
                              <DropdownMenuItem onSelect={() => toggleActive(t, true)}>Activate</DropdownMenuItem>
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {formTarget && (
        <AppointmentTypeFormModal
          mode={formTarget === 'add' ? 'create' : 'edit'}
          appointmentType={formTarget === 'add' ? null : formTarget}
          onClose={() => setFormTarget(null)}
          onSaved={handleSaved}
        />
      )}

      {viewTargetId !== null && (
        <AppointmentTypeViewDrawer
          appointmentTypeId={viewTargetId}
          onClose={() => setViewTargetId(null)}
          onEdit={(t) => {
            setViewTargetId(null)
            setFormTarget(t)
          }}
        />
      )}

      <AlertDialog open={deactivateTarget !== null} onOpenChange={(open) => !open && setDeactivateTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Deactivate {deactivateTarget?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This type will no longer be available for new bookings. Existing appointments and history that already
              use it are unaffected and will continue to display correctly. You can reactivate it at any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="danger"
              disabled={statusBusyId === deactivateTarget?.id}
              onClick={() => deactivateTarget && toggleActive(deactivateTarget, false)}
            >
              Deactivate
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}

// The ONE create/edit form -- reused by "+ Add Appointment Type" and
// the row-level "Edit" action, same as every other admin form in this
// app (PatientFormModal, AddDoctorModal, ...). Name only: duration,
// fee, doctor, and department are deliberately absent here -- they
// belong to the per-doctor assignment (Doctors -> assign appointment
// type), not the global type, and there is no description column on
// appointment_types to add a field for.
function AppointmentTypeFormModal({
  mode,
  appointmentType,
  onClose,
  onSaved,
}: {
  mode: 'create' | 'edit'
  appointmentType: AppointmentTypeAdminRow | null
  onClose: () => void
  onSaved: (t: AppointmentTypeAdminRow) => void
}) {
  const [name, setName] = useState(appointmentType?.name ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const saved =
        mode === 'edit' && appointmentType
          ? await updateAppointmentType(appointmentType.id, name.trim())
          : await createAppointmentType(name.trim())
      onSaved({ ...saved, doctor_count: appointmentType?.doctor_count ?? 0 })
      onClose()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : mode === 'edit'
            ? 'Could not update this appointment type'
            : 'Could not create the appointment type',
      )
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-label={mode === 'edit' ? 'Edit appointment type' : 'Add appointment type'}
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">{mode === 'edit' ? 'Edit Appointment Type' : 'Add Appointment Type'}</h3>
        {error && <p className="error">{error}</p>}

        <form onSubmit={handleSubmit}>
          <label className="inline-label" style={{ width: '100%' }}>
            Name
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="E.g. Consultation, Follow-up, Review, Procedure"
              required
            />
          </label>

          <p className="muted appointment-type-form-note">Duration and fees are configured for each doctor under Doctors → assign appointment type.</p>

          <div className="payment-form-actions" style={{ marginTop: 'var(--space-4)' }}>
            <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-sm" disabled={busy || !name.trim()}>
              {busy ? 'Saving…' : mode === 'edit' ? 'Save changes' : 'Create Type'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}

// Right-side detail drawer -- keeps the admin on the Appointment Types
// page (no navigation away) while showing the one thing the list
// table can't: which doctors actually offer this type, and at what
// duration/fee. Fetches its own detail (GET /appointment-types/{id})
// rather than relying on the list row, since the list only carries a
// count, not the per-doctor breakdown.
function AppointmentTypeViewDrawer({
  appointmentTypeId,
  onClose,
  onEdit,
}: {
  appointmentTypeId: number
  onClose: () => void
  onEdit: (t: AppointmentTypeAdminRow) => void
}) {
  const [detail, setDetail] = useState<AppointmentTypeDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    getAppointmentTypeDetail(appointmentTypeId)
      .then(setDetail)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load this appointment type'))
      .finally(() => setLoading(false))
  }, [appointmentTypeId])

  return createPortal(
    <div className="drawer-overlay" onClick={onClose}>
      <div
        className="drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Appointment type details"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        {loading && (
          <div className="state-block">
            <span className="spinner" aria-hidden="true" />
            Loading…
          </div>
        )}

        {error && <p className="error">{error}</p>}

        {!loading && detail && (
          <>
            <div className="appointment-type-drawer-heading">
              <h3>{detail.name}</h3>
              <span className={`pill status-${detail.active ? 'active' : 'inactive'}`}>
                {detail.active ? 'Active' : 'Inactive'}
              </span>
            </div>
            <p className="muted">
              Used by {detail.doctor_count} doctor{detail.doctor_count === 1 ? '' : 's'}. Duration and fees are
              configured per doctor.
            </p>

            <h4 className="appointment-type-drawer-subheading">Doctors offering this type</h4>

            {detail.doctors.length === 0 ? (
              <div className="state-block empty">
                <span className="state-icon" aria-hidden="true">
                  <Tag size={24} weight="light" />
                </span>
                No doctors currently offer this appointment type.
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Doctor</th>
                    <th>Duration</th>
                    <th>Fee</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.doctors.map((d) => (
                    <tr key={d.doctor_id}>
                      <td>
                        <strong>{d.doctor_name}</strong>
                        {d.department_name && <div className="muted">{d.department_name}</div>}
                      </td>
                      <td>{d.duration_minutes} min</td>
                      <td>₹{d.consultation_fee}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <div className="payment-form-actions" style={{ marginTop: 'var(--space-4)' }}>
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
                Close
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() =>
                  onEdit({ id: detail.id, name: detail.name, active: detail.active, doctor_count: detail.doctor_count })
                }
              >
                Edit
              </button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
