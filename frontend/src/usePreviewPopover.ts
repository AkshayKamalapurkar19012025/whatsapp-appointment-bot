import { useEffect, useLayoutEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from 'react'

export interface PreviewPopoverControls<TContainer extends HTMLElement> {
  open: boolean
  setOpen: Dispatch<SetStateAction<boolean>>
  // Attach to the element that contains both the trigger and the
  // popover -- an outside click is anything outside this element, so
  // it must wrap both, not just the trigger.
  containerRef: RefObject<TContainer | null>
  // Attach to the popover element itself, once it's open.
  popoverRef: RefObject<HTMLDivElement | null>
  // Horizontal correction (px) so the popover stays on-screen instead
  // of running off the left/right edge -- feed into a CSS var, e.g.
  // style={{ '--popover-shift': `${shift}px` }}.
  shift: number
  // True once the popover would run off the bottom of the viewport --
  // the caller decides what that means visually (DepartmentCard/
  // DoctorsPanel's own trigger, whose popover renders below its anchor
  // by default, flip to rendering above via a `.above` class).
  overflowsBottom: boolean
}

// Shared hover/focus preview-popover mechanics: viewport-edge collision
// avoidance (horizontal shift + a bottom-overflow flag) and dismissal
// (outside click, for touch's synthetic-mouse-event burst with no real
// pointerleave; Escape, for everyone). Extracted after DepartmentsPanel.
// tsx's DepartmentCard and DoctorsPanel.tsx's DoctorPreviewTrigger ended
// up with line-for-line identical copies of this same effect pair --
// originally modeled on MonthGrid.tsx's own popoverShift/popoverBelow,
// which stays a separate, hand-rolled copy since its multi-marker
// calendar (focusin-based dismissal across many markers, not one
// container) is a different enough shape not to force through this
// hook.
//
// What this does NOT own: how the popover opens (pointerType-gated
// hover, focus, tap -- callers differ here: a table row's hover trigger
// vs a card's whole-card hover trigger) or what it renders. Callers
// still wire their own onPointerEnter/onPointerLeave/onFocus/onBlur.
export function usePreviewPopover<TContainer extends HTMLElement = HTMLElement>(): PreviewPopoverControls<TContainer> {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<TContainer>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const [shift, setShift] = useState(0)
  const [overflowsBottom, setOverflowsBottom] = useState(false)

  useLayoutEffect(() => {
    if (!open) return
    const el = popoverRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const margin = 8
    let nextShift = 0
    if (rect.left < margin) {
      nextShift = margin - rect.left
    } else if (rect.right > window.innerWidth - margin) {
      nextShift = window.innerWidth - margin - rect.right
    }
    setShift(nextShift)
    setOverflowsBottom(rect.bottom > window.innerHeight - margin)
  }, [open])

  useEffect(() => {
    if (!open) return
    function handleOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('click', handleOutside, true)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('click', handleOutside, true)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [open])

  return { open, setOpen, containerRef, popoverRef, shift, overflowsBottom }
}
