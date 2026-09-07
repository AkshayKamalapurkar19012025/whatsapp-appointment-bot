import { ArrowLeft, CaretRight, House } from '@phosphor-icons/react'

// The shared "Back" / "Main Menu" footer for every step of the
// scheduling flow (SchedulingFlow.tsx) -- a step's own onBack callback
// pairs with a fixed onMainMenu (always startOver from the caller),
// rendered as two full-width, icon-led action cards rather than plain
// text links so the escape hatch out of a multi-step flow is as
// discoverable as the primary "next" action.
export default function StepActions({ onBack, onMainMenu }: { onBack: () => void; onMainMenu: () => void }) {
  return (
    <div className="step-actions">
      <button type="button" className="step-action-btn step-action-back" onClick={onBack}>
        <span className="step-action-icon" aria-hidden="true">
          <ArrowLeft size={10} weight="bold" />
        </span>
        <span className="step-action-text">
          <span className="step-action-label">Back</span>
          <span className="step-action-sub">Go to Previous Step</span>
        </span>
      </button>
      <button type="button" className="step-action-btn step-action-primary" onClick={onMainMenu}>
        <span className="step-action-icon" aria-hidden="true">
          <House size={10} weight="bold" />
        </span>
        <span className="step-action-text">
          <span className="step-action-label">Main Menu</span>
          <span className="step-action-sub">Return to Home</span>
        </span>
        <CaretRight size={9} className="step-action-arrow" aria-hidden="true" />
      </button>
    </div>
  )
}
