// Small inline stroke icons for the admin sidebar -- no icon-font/library
// dependency, just plain SVG so there's nothing to fail to load. 20x20,
// currentColor, so each one inherits the nav item's active/hover color
// automatically.

const common = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

export function IconCalendar() {
  return (
    <svg {...common} aria-hidden="true">
      <rect x="3" y="4.5" width="18" height="16" rx="2.5" />
      <path d="M3 9.5h18M8 2.5v4M16 2.5v4" />
      <path d="M8.5 13.5l2 2 4-4.2" />
    </svg>
  )
}

export function IconStethoscope() {
  return (
    <svg {...common} aria-hidden="true">
      <path d="M6 3.5v6a4 4 0 0 0 8 0v-6" />
      <path d="M6 3.5H4.5M14 3.5h1.5" />
      <path d="M14 9.5v2.5a6 6 0 0 1-12 0v-1.5" />
      <circle cx="18.5" cy="15.5" r="2.5" />
      <path d="M14 12v1a4.5 4.5 0 0 0 4.5 4.5" />
    </svg>
  )
}

export function IconBuilding() {
  return (
    <svg {...common} aria-hidden="true">
      <rect x="4" y="3.5" width="11" height="17" rx="1" />
      <rect x="15" y="9" width="5" height="11.5" rx="1" />
      <path d="M7.5 7.5h1M11 7.5h1M7.5 11h1M11 11h1M7.5 14.5h1M11 14.5h1" />
    </svg>
  )
}

export function IconTag() {
  return (
    <svg {...common} aria-hidden="true">
      <path d="M11.5 3.5H5a1.5 1.5 0 0 0-1.5 1.5v6.5a1.5 1.5 0 0 0 .44 1.06l8.5 8.5a1.5 1.5 0 0 0 2.12 0l6.5-6.5a1.5 1.5 0 0 0 0-2.12l-8.5-8.5a1.5 1.5 0 0 0-1.06-.44Z" />
      <circle cx="8" cy="8" r="1.4" />
    </svg>
  )
}

export function IconUsers() {
  return (
    <svg {...common} aria-hidden="true">
      <circle cx="9" cy="8" r="3.2" />
      <path d="M2.8 19.5c0-3.3 2.8-5.8 6.2-5.8s6.2 2.5 6.2 5.8" />
      <path d="M15.5 5a3.2 3.2 0 0 1 0 6.3" />
      <path d="M16 13.9c2.7.4 4.7 2.6 4.7 5.6" />
    </svg>
  )
}

export function IconShieldUser() {
  return (
    <svg {...common} aria-hidden="true">
      <path d="M12 2.8l7.2 2.8v6c0 4.8-3 8.6-7.2 9.6-4.2-1-7.2-4.8-7.2-9.6v-6L12 2.8Z" />
      <circle cx="12" cy="10" r="2.1" />
      <path d="M8.3 16c.6-1.7 2-2.7 3.7-2.7s3.1 1 3.7 2.7" />
    </svg>
  )
}
