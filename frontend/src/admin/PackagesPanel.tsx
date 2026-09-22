import { useEffect, useState } from 'react'
import { Archive, ArrowCounterClockwise, Gift, PencilSimple } from '@phosphor-icons/react'
import { ApiError, listPackages, listPackagesAdmin, updatePackageActive } from '../api'
import type { Package } from '../types'
import { useStaggerReveal } from '../useStaggerReveal'
import PackageFormModal from './PackageFormModal'

// Admin CRUD for the package catalog (OPD/HIMS master spec Phase 12,
// section 39) -- same table-plus-modal shape as the simpler admin
// lists in this app, not the richer card-grid DepartmentsPanel uses
// (packages have no doctor-assignment concept to warrant that). A
// package billed onto a live invoice is picked from here via
// AppointmentBillingPanel's "Bill a package" action, not managed from
// there -- this page is the catalog, that one is where it gets spent.
//
// isAdmin gates the same way DepartmentsPanel/AppointmentTypesPanel
// already do: package.manage (create/edit/deactivate) is ADMIN-only
// server-side, so a plain STAFF session sees a read-only list (via the
// bare-staff, active-only GET, not the ADMIN-only /admin listing that
// would 403) with no Add/Edit/Deactivate controls, avoiding a
// pointless round trip for something they're never authorized to do.
export default function PackagesPanel({ isAdmin }: { isAdmin: boolean }) {
  const [packages, setPackages] = useState<Package[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [formTarget, setFormTarget] = useState<'add' | Package | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const tbodyRef = useStaggerReveal<HTMLTableSectionElement>([packages])

  function load() {
    setLoading(true)
    ;(isAdmin ? listPackagesAdmin() : listPackages())
      .then(setPackages)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load packages'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  function handleSaved(saved: Package) {
    setPackages((prev) => {
      const exists = prev.some((p) => p.id === saved.id)
      if (exists) return prev.map((p) => (p.id === saved.id ? saved : p))
      return [...prev, saved]
    })
  }

  async function toggleActive(pkg: Package) {
    setBusyId(pkg.id)
    setError(null)
    try {
      await updatePackageActive(pkg.id, !pkg.active)
      setPackages((prev) => prev.map((p) => (p.id === pkg.id ? { ...p, active: !p.active } : p)))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update this package')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Packages</h2>
          <p className="muted">Priced bundles billed as a single line item on a patient's bill.</p>
        </div>
        {isAdmin && (
          <button type="button" className="btn btn-sm" onClick={() => setFormTarget('add')}>
            + Add Package
          </button>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading packages…
        </div>
      )}
      {!loading && packages.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <Gift size={28} weight="light" />
          </span>
          No packages yet. Add one to bill it as a single line item on a visit.
        </div>
      )}

      {!loading && packages.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th>Price</th>
              <th>Status</th>
              {isAdmin && <th>Actions</th>}
            </tr>
          </thead>
          <tbody ref={tbodyRef}>
            {packages.map((p) => (
              <tr key={p.id}>
                <td>
                  <strong>{p.name}</strong>
                </td>
                <td className="muted">{p.description || '—'}</td>
                <td>₹{p.price.toFixed(2)}</td>
                <td>
                  <span className={`pill status-${p.active ? 'confirmed' : 'cancelled'}`}>
                    {p.active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                {isAdmin && (
                  <td>
                    <div className="card-grid-item-actions">
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() => setFormTarget(p)}
                        aria-label={`Edit ${p.name}`}
                      >
                        <PencilSimple size={16} />
                      </button>
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() => toggleActive(p)}
                        disabled={busyId === p.id}
                        aria-label={p.active ? `Deactivate ${p.name}` : `Reactivate ${p.name}`}
                      >
                        {p.active ? <Archive size={16} /> : <ArrowCounterClockwise size={16} />}
                      </button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {formTarget && (
        <PackageFormModal
          mode={formTarget === 'add' ? 'create' : 'edit'}
          pkg={formTarget === 'add' ? null : formTarget}
          onClose={() => setFormTarget(null)}
          onSaved={handleSaved}
        />
      )}
    </section>
  )
}
