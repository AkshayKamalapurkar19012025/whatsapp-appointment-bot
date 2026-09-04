import { useEffect, useRef, useState, type ReactElement } from 'react'
import { ArrowUpRight, List, X } from '@phosphor-icons/react'
import gsap from 'gsap'

export interface NavMenuItem {
  key: string
  label: string
  icon: ReactElement
  active?: boolean
  disabled?: boolean
  disabledReason?: string
  onSelect?: () => void
}

// Full-screen "reveal a large visual per hovered item" navigation menu,
// in place of the old always-visible sidebar -- same interaction concept
// as a large numbered list menu that shows a big illustration for
// whichever row the pointer is over (see AdminApp.tsx's comment for
// which real reference this restyles: existing brand colors/typography,
// not that site's own branding/assets/copy). Every existing section is
// still just a button that flips `section` state in AdminApp -- this
// component only changes how that choice is presented, not what any of
// it does or which roles can reach which item (adminOnly filtering,
// RBAC enforcement, etc. all still live in AdminApp/the backend).
//
// Desktop (hover-capable) pointers get the large reveal panel; touch/
// coarse pointers never receive hover events at all, so they just get
// the plain numbered list with each row's own inline icon -- there is
// no two-step "reveal then tap" on mobile, tapping a row navigates
// immediately, per explicit responsive requirement.
export default function NavMenu({
  open,
  onClose,
  items,
  footerItems,
}: {
  open: boolean
  onClose: () => void
  items: NavMenuItem[]
  footerItems?: { key: string; label: string; onSelect: () => void }[]
}) {
  const [hovered, setHovered] = useState<string | null>(null)
  const overlayRef = useRef<HTMLDivElement | null>(null)
  const visualRef = useRef<HTMLDivElement | null>(null)

  // Every close path (Escape, the X button, picking an item, a footer
  // link) goes through this so the hovered/revealed item never carries
  // over stale into the next time the menu opens.
  function close() {
    setHovered(null)
    onClose()
  }

  // Keyboard escape to close -- same "clear escape route" expectation as
  // every other modal/overlay in this app (see components/ui/alert-
  // dialog.tsx).
  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!open) return
    const container = overlayRef.current
    if (!container) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    gsap.fromTo(
      container,
      { opacity: 0 },
      { opacity: 1, duration: 0.25, ease: 'power2.out' },
    )
    const rows = container.querySelectorAll('.nav-menu-item')
    gsap.fromTo(
      rows,
      { opacity: 0, y: 14 },
      { opacity: 1, y: 0, duration: 0.35, ease: 'power2.out', stagger: 0.04, delay: 0.05 },
    )
  }, [open])

  useEffect(() => {
    const visual = visualRef.current
    if (!visual) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    gsap.fromTo(
      visual,
      { opacity: 0, scale: 0.92 },
      { opacity: 1, scale: 1, duration: 0.3, ease: 'power2.out', overwrite: true },
    )
  }, [hovered])

  if (!open) return null

  const visualItem = items.find((i) => i.key === hovered) ?? items.find((i) => i.active) ?? items[0]

  return (
    <div
      className="nav-menu-overlay"
      ref={overlayRef}
      role="dialog"
      aria-modal="true"
      aria-label="Navigation menu"
    >
      <button type="button" className="nav-menu-close" onClick={close} aria-label="Close menu">
        <X size={22} weight="bold" />
      </button>

      <div className="nav-menu-body">
        <ul className="nav-menu-list" onMouseLeave={() => setHovered(null)}>
          {items.map((item, index) => (
            <li key={item.key}>
              <button
                type="button"
                className={`nav-menu-item${item.active ? ' active' : ''}${item.disabled ? ' disabled' : ''}`}
                disabled={item.disabled}
                onMouseEnter={() => setHovered(item.key)}
                onFocus={() => setHovered(item.key)}
                onClick={() => {
                  if (item.disabled) return
                  item.onSelect?.()
                  close()
                }}
              >
                <span className="nav-menu-item-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="nav-menu-item-icon" aria-hidden="true">
                  {item.icon}
                </span>
                <span className="nav-menu-item-label">
                  {item.label}
                  {item.disabled && <span className="nav-menu-item-badge">Coming soon</span>}
                </span>
                <span className="nav-menu-item-arrow" aria-hidden="true">
                  <ArrowUpRight size={22} weight="bold" />
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="nav-menu-visual" aria-hidden="true">
          <div className="nav-menu-visual-card" ref={visualRef} key={visualItem?.key}>
            <span className="nav-menu-visual-icon">{visualItem?.icon}</span>
          </div>
        </div>
      </div>

      {footerItems && footerItems.length > 0 && (
        <div className="nav-menu-footer">
          {footerItems.map((f) => (
            <button
              key={f.key}
              type="button"
              className="link"
              onClick={() => {
                f.onSelect()
                close()
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function NavMenuToggle({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="nav-menu-toggle" onClick={onClick} aria-label="Open menu">
      <List size={20} weight="bold" />
    </button>
  )
}
