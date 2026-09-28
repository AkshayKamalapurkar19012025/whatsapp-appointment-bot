import { useEffect, useRef, useState } from 'react'
import { listMedications } from '../api'
import type { Medication } from '../types'

// Medication Master (OPD/HIMS interoperability master prompt Phase 5,
// migrations/0054_medication_master.sql) -- a small, self-contained
// search-and-pick widget shared by the prescribing form
// (PrescriptionPanel.tsx) and the stock/medication admin forms
// (PharmacyPanel.tsx). It owns its own search box, separate from
// whatever plain medicine-name text field it sits next to: picking a
// result here calls onSelect(medication), which the caller uses to
// fill its own free-text fields and record medication_id alongside
// them. A clinician who never touches this box can still type a
// medicine name directly, exactly as before this phase -- this is a
// shortcut, not a requirement. Only ever shows display_name (generic
// (brand) strength form) to the user, never an internal id.
export default function MedicationPicker({ onSelect }: { onSelect: (medication: Medication) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Medication[]>([])
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setResults([])
      return
    }
    const handle = setTimeout(() => {
      listMedications(trimmed)
        .then((meds) => setResults(meds))
        .catch(() => setResults([]))
    }, 250)
    return () => clearTimeout(handle)
  }, [query])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  return (
    <div ref={containerRef} className="medication-picker" style={{ position: 'relative' }}>
      <input
        type="text"
        value={query}
        placeholder="Search medication master (optional)…"
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
      />
      {open && results.length > 0 && (
        <ul
          className="medication-picker-results"
          style={{
            position: 'absolute',
            zIndex: 20,
            background: 'var(--card-bg, #fff)',
            border: '1px solid var(--border-color, #ccc)',
            borderRadius: 4,
            margin: 0,
            padding: 0,
            listStyle: 'none',
            width: '100%',
            maxHeight: 220,
            overflowY: 'auto',
          }}
        >
          {results.map((med) => (
            <li key={med.id}>
              <button
                type="button"
                className="medication-picker-option"
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '6px 10px',
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                }}
                onClick={() => {
                  onSelect(med)
                  setQuery('')
                  setResults([])
                  setOpen(false)
                }}
              >
                {med.display_name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
