import * as React from 'react'
import * as AlertDialogPrimitive from '@radix-ui/react-alert-dialog'
import { cn } from '../../lib/utils'

// shadcn/ui-style wrapper around Radix's AlertDialog primitive (hand-
// authored to this repo's existing color tokens via Tailwind's
// bg-[var(--...)] arbitrary-value syntax, rather than a separate
// Tailwind color palette, so it matches the rest of the app exactly).
// Used for confirm-before-destructive-action prompts (see
// styles.css/ui-ux-pro-max's own `confirmation-dialogs` rule) -- this
// replaces the two window.confirm() calls in the app (cancelling an
// appointment, admin and patient side), which can't be styled at all
// and look jarring next to the rest of this design.

const AlertDialog = AlertDialogPrimitive.Root
const AlertDialogTrigger = AlertDialogPrimitive.Trigger
const AlertDialogPortal = AlertDialogPrimitive.Portal

function AlertDialogOverlay({ className, ...props }: React.ComponentProps<typeof AlertDialogPrimitive.Overlay>) {
  return (
    <AlertDialogPrimitive.Overlay
      // z-[1200]: higher than .modal-overlay's plain-CSS z-index:1000
      // (styles.css) and TimeCombobox's z-[1100] -- a confirm dialog
      // (e.g. ConfigureScheduleModal's "Discard this schedule?") must
      // always render on top of any modal it's nested inside, not be
      // silently hidden behind it (Tailwind's z-50 = z-index 50 lost to
      // that fight even though both portal to document.body).
      className={cn('alert-dialog-overlay fixed inset-0 z-[1200] bg-black/40 backdrop-blur-sm', className)}
      {...props}
    />
  )
}

function AlertDialogContent({ className, ...props }: React.ComponentProps<typeof AlertDialogPrimitive.Content>) {
  return (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Content
        className={cn(
          'alert-dialog-content fixed left-1/2 top-1/2 z-[1200] w-full max-w-[420px] -translate-x-1/2 -translate-y-1/2',
          'rounded-[var(--radius-lg)] border border-[var(--color-glass-border)] bg-[var(--color-glass)] p-6',
          'shadow-[var(--shadow-lg)] backdrop-blur-[20px]',
          className,
        )}
        {...props}
      />
    </AlertDialogPortal>
  )
}

function AlertDialogHeader({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('mb-4', className)} {...props} />
}

function AlertDialogTitle({ className, ...props }: React.ComponentProps<typeof AlertDialogPrimitive.Title>) {
  return (
    <AlertDialogPrimitive.Title
      className={cn('m-0 text-[1.1rem] font-bold text-[var(--color-text)]', className)}
      {...props}
    />
  )
}

function AlertDialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Description>) {
  return (
    <AlertDialogPrimitive.Description
      className={cn('mt-2 text-[0.92rem] leading-relaxed text-[var(--color-text-secondary)]', className)}
      {...props}
    />
  )
}

function AlertDialogFooter({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('mt-6 flex justify-end gap-3', className)} {...props} />
}

function AlertDialogCancel({ className, ...props }: React.ComponentProps<typeof AlertDialogPrimitive.Cancel>) {
  return (
    <AlertDialogPrimitive.Cancel
      className={cn(
        'inline-flex cursor-pointer items-center justify-center rounded-[var(--radius-sm)] border border-[var(--color-border-strong)]',
        'bg-[var(--color-surface)] px-4 py-2.5 text-[0.95rem] font-semibold text-[var(--color-primary-hover)]',
        'transition-colors hover:bg-[var(--color-primary-soft)] hover:border-[var(--color-primary)]',
        className,
      )}
      {...props}
    />
  )
}

function AlertDialogAction({
  className,
  variant = 'default',
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Action> & { variant?: 'default' | 'danger' }) {
  return (
    <AlertDialogPrimitive.Action
      className={cn(
        'inline-flex cursor-pointer items-center justify-center rounded-[var(--radius-sm)] border border-transparent',
        'px-4 py-2.5 text-[0.95rem] font-semibold text-[var(--color-on-primary)] transition-colors',
        variant === 'danger'
          ? 'bg-[var(--color-danger)] hover:bg-[#8f1f19]'
          : 'bg-[var(--color-primary)] hover:bg-[var(--color-primary-hover)]',
        className,
      )}
      {...props}
    />
  )
}

export {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
}
