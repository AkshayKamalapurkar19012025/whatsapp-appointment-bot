import { useEffect, useState } from 'react'
import { ApiError, listHospitalModules, setModuleEnabled, setModuleLicensed } from '../api'
import type { HospitalModule, ModuleKey } from '../types'

const DEGRADATION_LABELS: Record<HospitalModule['degradation'], string> = {
  EXTERNAL: 'Routes through an external referral instead',
  BLOCKED: 'New use is blocked; existing records stay visible',
  HIDDEN: 'Disappears from the sidebar; existing records stay visible',
}

// OPD/HIMS master spec sections 67-68: Licensed (a platform-level
// entitlement) is deliberately a separate switch from Enabled (the
// hospital admin's own on/off, only reachable once licensed) -- "a
// hospital admin must NOT be able to self-grant paid modules". This
// app has no separate platform-owner role yet, so both switches live
// on one ADMIN-only screen rather than two (module.manage_license/
// module.manage_enablement, migrations/0053, are still two distinct
// permissions underneath, so a future platform role only needs a
// role_permissions change, not a new screen).
//
// hospitalId is always the acting admin's own hospital_id -- this app
// is single-tenant in practice (one hospital, one ADMIN role covering
// what would eventually be two), so there's no hospital picker here.
export default function ModuleLicensingPanel({ hospitalId }: { hospitalId: number }) {
  const [modules, setModules] = useState<HospitalModule[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState<ModuleKey | null>(null)

  function load() {
    setLoading(true)
    listHospitalModules(hospitalId)
      .then(setModules)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load modules'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [hospitalId])

  async function toggleLicensed(mod: HospitalModule) {
    setBusyKey(mod.module_key)
    setError(null)
    try {
      const result = await setModuleLicensed(hospitalId, mod.module_key, !mod.licensed)
      setModules((prev) =>
        prev.map((m) =>
          m.module_key === mod.module_key
            ? { ...m, licensed: result.licensed, enabled: result.enabled, available: result.licensed && result.enabled }
            : m,
        ),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update this module')
    } finally {
      setBusyKey(null)
    }
  }

  async function toggleEnabled(mod: HospitalModule) {
    setBusyKey(mod.module_key)
    setError(null)
    try {
      const result = await setModuleEnabled(hospitalId, mod.module_key, !mod.enabled)
      setModules((prev) =>
        prev.map((m) =>
          m.module_key === mod.module_key
            ? { ...m, licensed: result.licensed, enabled: result.enabled, available: result.licensed && result.enabled }
            : m,
        ),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update this module')
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Module Licensing</h2>
          <p className="muted">
            Licensed is a platform-level entitlement; Enabled is this hospital's own switch, reachable only once a
            module is licensed.
          </p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}

      {!loading && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Module</th>
              <th>If disabled</th>
              <th>Licensed</th>
              <th>Enabled</th>
              <th>Available</th>
            </tr>
          </thead>
          <tbody>
            {modules.map((m) => (
              <tr key={m.module_key}>
                <td>
                  <strong>{m.name}</strong>
                  <div className="muted">{m.description}</div>
                </td>
                <td className="muted">{DEGRADATION_LABELS[m.degradation]}</td>
                <td>
                  <label className="inline-label checkbox-label">
                    <input
                      type="checkbox"
                      checked={m.licensed}
                      disabled={busyKey === m.module_key}
                      onChange={() => toggleLicensed(m)}
                      aria-label={`${m.name} licensed`}
                    />
                  </label>
                </td>
                <td>
                  <label className="inline-label checkbox-label">
                    <input
                      type="checkbox"
                      checked={m.enabled}
                      disabled={busyKey === m.module_key || !m.licensed}
                      onChange={() => toggleEnabled(m)}
                      aria-label={`${m.name} enabled`}
                      title={!m.licensed ? 'License this module before enabling it' : undefined}
                    />
                  </label>
                </td>
                <td>
                  <span className={`pill status-${m.available ? 'confirmed' : 'cancelled'}`}>
                    {m.available ? 'Available' : 'Unavailable'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
