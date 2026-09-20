import * as React from 'react'
import * as SelectPrimitive from '@radix-ui/react-select'
import { CaretDown, CaretUp, Check } from '@phosphor-icons/react'
import { cn } from '../../lib/utils'

// shadcn/ui-style wrapper around Radix's Select primitive, styled to
// this repo's existing tokens (see alert-dialog.tsx's note on why:
// bg-[var(--...)] arbitrary values instead of a separate Tailwind
// palette, so it matches the rest of the app exactly). Used in place of
// a plain <select> where the native control's complete lack of styling
// (no way to theme the dropdown popover/options at all, browser-
// inconsistent) was the most visible gap versus the rest of this
// design -- the admin Appointments filter bar.

const Select = SelectPrimitive.Root
const SelectValue = SelectPrimitive.Value

function SelectTrigger({ className, children, ...props }: React.ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        'flex w-full cursor-pointer items-center justify-between gap-2 rounded-[var(--radius-sm)]',
        'border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3.5 py-2.5',
        'text-[0.95rem] text-[var(--color-text)] transition-colors hover:border-[var(--color-text-muted)]',
        'focus:outline-none focus:border-[var(--color-primary)] focus:ring-[3px] focus:ring-[var(--color-primary-soft)]',
        'data-[placeholder]:text-[var(--color-text-muted)]',
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <CaretDown size={16} weight="bold" className="opacity-60" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
}

function SelectContent({ className, children, position = 'popper', ...props }: React.ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        position={position}
        className={cn(
          // z-[1100]: higher than .modal-overlay's plain-CSS z-index:1000
          // (styles.css) -- same fix already applied to dropdown-menu.tsx
          // and time-combobox.tsx, for the same reason (Tailwind's z-50 =
          // z-index 50 loses to that plain-CSS 1000, so this popover would
          // render behind any modal that hosts a Select, visible nowhere
          // and unclickable even though it's technically "open").
          'select-content-anim z-[1100] max-h-72 min-w-[8rem] overflow-hidden rounded-[var(--radius-md)]',
          'border border-[var(--color-border)] bg-[var(--color-surface)] shadow-[var(--shadow-lg)]',
          position === 'popper' && 'w-[var(--radix-select-trigger-width)]',
          className,
        )}
        {...props}
      >
        <SelectPrimitive.ScrollUpButton className="flex items-center justify-center py-1">
          <CaretUp size={14} />
        </SelectPrimitive.ScrollUpButton>
        <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
        <SelectPrimitive.ScrollDownButton className="flex items-center justify-center py-1">
          <CaretDown size={14} />
        </SelectPrimitive.ScrollDownButton>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
}

function SelectItem({ className, children, ...props }: React.ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      className={cn(
        'relative flex cursor-pointer select-none items-center rounded-[var(--radius-sm)] py-2 pl-8 pr-3',
        'text-[0.92rem] text-[var(--color-text)] outline-none',
        'data-[highlighted]:bg-[var(--color-primary-soft)] data-[highlighted]:text-[var(--color-primary-hover)]',
        'data-[state=checked]:font-semibold',
        className,
      )}
      {...props}
    >
      <span className="absolute left-2.5 flex h-4 w-4 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check size={14} weight="bold" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  )
}

export { Select, SelectValue, SelectTrigger, SelectContent, SelectItem }
