import { useEffect, useState } from 'react'
import { ApiError, createStaffAccount, listStaffAccounts, setStaffAccountActive } from '../api'
import type { StaffAccount } from '../types'
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

export default function StaffAccountsPanel() {
  const [accounts, setAccounts] = useState<StaffAccount[]>([])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'ADMIN' | 'STAFF'>('STAFF')
  const [toggleTarget, setToggleTarget] = useState<StaffAccount | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  function load() {
    setLoading(true)
    listStaffAccounts()
      .then(setAccounts)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : 'Could not load staff accounts'),
      )
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  function resetCreateForm() {
    setUsername('')
    setPassword('')
    setRole('STAFF')
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await createStaffAccount(username, password, role)
      resetCreateForm()
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create staff account')
    } finally {
      setBusy(false)
    }
  }

  async function confirmToggleActive() {
    if (!toggleTarget) return
    setError(null)
    try {
      await setStaffAccountActive(toggleTarget.id, !toggleTarget.active)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update account')
    } finally {
      setToggleTarget(null)
    }
  }

  return (
    <section>
      <h2>Staff Accounts</h2>
      {error && <p className="error">{error}</p>}

      <form className="inline-form wrap" onSubmit={handleCreate}>
        <input
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <input
          type="password"
          placeholder="Password (min 8 characters)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          required
        />
        <select value={role} onChange={(e) => setRole(e.target.value as 'ADMIN' | 'STAFF')}>
          <option value="STAFF">STAFF</option>
          <option value="ADMIN">ADMIN</option>
        </select>
        <button type="submit" style={{ width: 'auto' }} disabled={busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        {(username || password) && (
          <button type="button" className="btn-secondary btn" style={{ width: 'auto' }} onClick={resetCreateForm}>
            Cancel
          </button>
        )}
      </form>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading staff accounts…
        </div>
      )}

      {!loading && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Username</th>
              <th>Role</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <tr key={a.id}>
                <td>{a.username}</td>
                <td>
                  <span className={`pill role-${a.role.toLowerCase()}`}>{a.role}</span>
                </td>
                <td>
                  <span className={`pill ${a.active ? 'status-booked' : 'status-cancelled'}`}>
                    {a.active ? 'Active' : 'Deactivated'}
                  </span>
                </td>
                <td>
                  <button type="button" className="link" onClick={() => setToggleTarget(a)}>
                    {a.active ? 'Deactivate' : 'Reactivate'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <AlertDialog open={toggleTarget !== null} onOpenChange={(open) => !open && setToggleTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{toggleTarget?.active ? 'Deactivate this account?' : 'Reactivate this account?'}</AlertDialogTitle>
            <AlertDialogDescription>
              {toggleTarget &&
                (toggleTarget.active
                  ? `"${toggleTarget.username}" will no longer be able to log in.`
                  : `"${toggleTarget.username}" will be able to log in again.`)}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant={toggleTarget?.active ? 'danger' : 'default'}
              onClick={confirmToggleActive}
            >
              {toggleTarget?.active ? 'Deactivate' : 'Reactivate'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
