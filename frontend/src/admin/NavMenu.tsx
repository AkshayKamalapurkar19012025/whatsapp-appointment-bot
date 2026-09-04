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

// Left-side "reveal a visual per hovered item" navigation drawer, in
// place of the old always-visible sidebar -- same interaction concept
// as a numbered list menu that shows a preview illustration for
// whichever row the pointer is over (see AdminApp.tsx's comment for
// which real reference this restyles: existing brand colors/typography,
// not that site's own branding/assets/copy), adapted to a narrower
// left-edge panel instead of a full-screen takeover. Every existing
// section is still just a button that flips `section` state in
// AdminApp -- this component only changes how that choice is
// presented, not what any of it does or which roles can reach which
// item (adminOnly filtering, RBAC enforcement, etc. all still live in
// AdminApp/the backend).
//
// Desktop (hover-capable) pointers get the preview panel reacting to
// hover; touch/coarse pointers never receive hover events at all, so
// they just get the plain numbered list with each row's own inline
// icon -- there is no two-step "reveal then tap" on mobile, tapping a
// row navigates immediately, per explicit responsive requirement.
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
  // `rendered` stays true slightly longer than `open` so the drawer can
  // play its slide-out animation before actually leaving the DOM --
  // without this the drawer would just vanish instantly on close, which
  // reads as broken for a panel whose whole identity is "slides in from
  // the edge."
  const [rendered, setRendered] = useState(false)
  const drawerRef = useRef<HTMLDivElement | null>(null)
  const backdropRef = useRef<HTMLDivElement | null>(null)
  const visualRef = useRef<HTMLDivElement | null>(null)

  function reducedMotion() {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  }

  // Every close path (Escape, the X button, the backdrop, picking an
  // item, a footer link) goes through this so the hovered/revealed item
  // never carries over stale into the next time the menu opens.
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

  // Mount as soon as `open` goes true; unmounting is deferred to the
  // exit-animation effect below.
  useEffect(() => {
    if (open) setRendered(true)
  }, [open])

  // Slide-in entrance, keyed on `rendered` (not `open`) so it only fires
  // once the drawer is actually in the DOM and drawerRef is populated.
  useEffect(() => {
    if (!rendered) return
    const drawer = drawerRef.current
    if (!drawer) return
    if (reducedMotion()) return
    gsap.set(drawer, { xPercent: -100 })
    gsap.to(drawer, { xPercent: 0, duration: 0.32, ease: 'power2.out' })
    if (backdropRef.current) {
      gsap.fromTo(backdropRef.current, { opacity: 0 }, { opacity: 1, duration: 0.25, ease: 'power2.out' })
    }
    const rows = drawer.querySelectorAll('.nav-menu-item')
    gsap.fromTo(
      rows,
      { opacity: 0, x: -12 },
      { opacity: 1, x: 0, duration: 0.3, ease: 'power2.out', stagger: 0.035, delay: 0.08 },
    )
  }, [rendered])

  // Slide-out exit, keyed on `open` -- only runs when closing a drawer
  // that's currently rendered, and removes it from the DOM once the
  // animation completes.
  useEffect(() => {
    if (open) return
    if (!rendered) return
    const drawer = drawerRef.current
    if (!drawer || reducedMotion()) {
      setRendered(false)
      return
    }
    const tl = gsap.timeline({ onComplete: () => setRendered(false) })
    tl.to(drawer, { xPercent: -100, duration: 0.26, ease: 'power2.in' }, 0)
    if (backdropRef.current) {
      tl.to(backdropRef.current, { opacity: 0, duration: 0.2, ease: 'power2.in' }, 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    const visual = visualRef.current
    if (!visual) return
    if (reducedMotion()) return
    gsap.fromTo(
      visual,
      { opacity: 0, scale: 0.94 },
      { opacity: 1, scale: 1, duration: 0.28, ease: 'power2.out', overwrite: true },
    )
  }, [hovered])

  if (!rendered) return null

  const visualItem = items.find((i) => i.key === hovered) ?? items.find((i) => i.active) ?? items[0]

  return (
    <>
      <div
        className="nav-menu-backdrop fixed inset-0 z-[299] bg-black/40 backdrop-blur-sm"
        ref={backdropRef}
        onClick={close}
        aria-hidden="true"
      />
      <div
        className="nav-menu-drawer"
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="Navigation menu"
      >
        <div className="nav-menu-drawer-header">
          <button type="button" className="nav-menu-close" onClick={close} aria-label="Close menu">
            <X size={20} weight="bold" />
          </button>
        </div>

        <div className="nav-menu-visual" aria-hidden="true">
          <div className="nav-menu-visual-card" ref={visualRef} key={visualItem?.key}>
            <span className="nav-menu-visual-icon">{visualItem?.icon}</span>
          </div>
        </div>

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
                  <ArrowUpRight size={20} weight="bold" />
                </span>
              </button>
            </li>
          ))}
        </ul>

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
    </>
  )
}

export function NavMenuToggle({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="nav-menu-toggle" onClick={onClick} aria-label="Open menu">
      <List size={20} weight="bold" />
    </button>
  )
}
