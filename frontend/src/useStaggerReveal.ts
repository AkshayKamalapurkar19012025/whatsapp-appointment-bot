import { useEffect, useRef } from 'react'
import gsap from 'gsap'

// Fades + rises the direct children of the returned ref's element into
// place whenever `deps` changes -- for lists that populate after an
// async load (option lists, appointment cards, table rows, slot chips)
// so they arrive with a bit of life instead of popping in instantly.
// Re-fires on every dep change, not just mount, so switching doctors/
// filters restages the new list too.
//
// Respects prefers-reduced-motion itself (rather than relying only on
// styles.css's global `animation-duration: 0.001ms` rule, which doesn't
// reach GSAP's JS-driven tweens) by skipping the animation entirely --
// children just render at their normal opacity/position.
export function useStaggerReveal<T extends HTMLElement>(
  deps: unknown[],
  // Defaults to the container's direct children. Pass a selector (e.g.
  // '.slot-chip') for layouts like SlotGrid's, where the actual items
  // sit nested inside several group wrapper divs rather than as direct
  // children of one list container.
  selector?: string,
) {
  const ref = useRef<T | null>(null)

  useEffect(() => {
    const container = ref.current
    if (!container) return

    const items = selector ? Array.from(container.querySelectorAll(selector)) : Array.from(container.children)
    if (items.length === 0) return

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    // Cap the total cascade at ~0.5s regardless of list length -- a
    // fixed 40ms-per-item delay looks great for 5-10 rows but turns
    // into an multi-second crawl on a 100-row admin table.
    const perItemDelay = Math.min(0.04, 0.5 / items.length)

    gsap.fromTo(
      items,
      { opacity: 0, y: 8 },
      { opacity: 1, y: 0, duration: 0.35, ease: 'power2.out', stagger: perItemDelay, overwrite: true }
    )
    // deps is intentionally caller-supplied (this hook exists precisely
    // so callers can pass whatever list/tab/filter state should trigger
    // a re-reveal) rather than a static array literal -- the standard,
    // documented shape for a generic custom hook wrapping useEffect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return ref
}
