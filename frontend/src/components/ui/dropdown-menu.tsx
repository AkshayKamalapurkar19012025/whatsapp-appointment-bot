import * as React from 'react'
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu'
import { cn } from '../../lib/utils'

// shadcn/ui-style wrapper around Radix's DropdownMenu primitive, same
// treatment as select.tsx/alert-dialog.tsx (this repo's existing color
// tokens via Tailwind's bg-[var(--...)] arbitrary-value syntax rather
// than a separate Tailwind palette). Used for the top-right account
// menu (AdminSidebar.tsx) -- the one place this app needs a small,
// anchored popover menu rather than a full-width <select> or a modal.

const DropdownMenu = DropdownMenuPrimitive.Root
const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger

function DropdownMenuContent({
  className,
  sideOffset = 8,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        sideOffset={sideOffset}
        className={cn(
          // z-[1100]: above .modal-overlay's z-index:1000 (styles.css)
          // -- both this menu's Portal and the modal's own portal
          // (AppointmentDetailsModal.tsx etc.) render into document.body
          // as siblings, so when this menu opens *from inside* a modal
          // (e.g. its "..." overflow button), it must outrank the
          // modal's own stacking or it renders hidden behind it. Plain
          // z-50 only ever worked because every prior use of this menu
          // opened outside any modal.
          'dropdown-menu-content-anim z-[1100] min-w-[10rem] overflow-hidden rounded-[var(--radius-md)] p-1',
          'border border-[var(--color-border)] bg-[var(--color-surface)] shadow-[var(--shadow-lg)]',
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  )
}

function DropdownMenuItem({
  className,
  variant = 'default',
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Item> & { variant?: 'default' | 'danger' }) {
  return (
    <DropdownMenuPrimitive.Item
      className={cn(
        'flex cursor-pointer select-none items-center gap-2 rounded-[var(--radius-sm)] px-3 py-2',
        'text-[0.92rem] font-medium outline-none transition-colors',
        variant === 'danger'
          ? 'text-[var(--color-danger)] data-[highlighted]:bg-[var(--color-danger-soft)]'
          : 'text-[var(--color-text)] data-[highlighted]:bg-[var(--color-primary-soft)] data-[highlighted]:text-[var(--color-primary-hover)]',
        className,
      )}
      {...props}
    />
  )
}

function DropdownMenuSeparator({ className, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return (
    <DropdownMenuPrimitive.Separator
      className={cn('my-1 h-px bg-[var(--color-border)]', className)}
      {...props}
    />
  )
}

export { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator }
