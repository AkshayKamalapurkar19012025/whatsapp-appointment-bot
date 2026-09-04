import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// Standard shadcn/ui utility: merges conditional class lists (clsx) and
// then resolves conflicting Tailwind classes so the last one wins
// (tailwind-merge) -- e.g. cn('px-2', condition && 'px-4') correctly
// keeps only px-4 instead of leaving both in the class list.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
