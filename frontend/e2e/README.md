# Frontend end-to-end tests (Playwright)

The first committed browser test suite in this repo. Everything before it
(`frontend/README.md`'s "verified with a real Chromium browser via Playwright
during development") was ad-hoc, throwaway scripting, never a committed spec.

These drive the **real** admin UI against a **real** backend and database —
nothing is mocked. That's deliberate: the bugs these cover (a silently
swallowed per-doctor queue fetch rendering as a normal status, a poll that
stops for non-today date scopes) only exist in the seam between the frontend
and live API responses, which a mocked test can't see.

## One-time setup

They need their own database, separate from both the dev database and the
`*_test` one `tests/conftest.py` truncates between pytest runs:

```bash
sudo -u postgres psql -c "CREATE DATABASE appointment_bot_e2e OWNER <your-db-user>;"
DATABASE_URL="postgresql://<user>:<pass>@localhost:5432/appointment_bot_e2e" python scripts/migrate.py
```

There is no self-service staff signup (see `app/services/staff_management.py`),
so the fixed account every spec logs in as is created directly, once:

```bash
DATABASE_URL="postgresql://<user>:<pass>@localhost:5432/appointment_bot_e2e" python -c "
import psycopg
from app.services.staff_management import create_staff_account
conn = psycopg.connect('postgresql://<user>:<pass>@localhost:5432/appointment_bot_e2e')
with conn.cursor() as cur:
    create_staff_account(cur, 'e2e-admin', 'e2e-password-123', 'ADMIN')
conn.commit()
"
```

## Running

Start the backend against that database, then run the suite — Playwright boots
the Vite dev server itself (`webServer` in `playwright.config.ts`) and reuses
one already running:

```bash
DATABASE_URL="postgresql://<user>:<pass>@localhost:5432/appointment_bot_e2e" \
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &

cd frontend && npx playwright test
```

Chromium comes from `PLAYWRIGHT_BROWSERS_PATH` (pre-installed in this
environment); `playwright.config.ts` points at it explicitly rather than
downloading one.

## Conventions these specs follow

- **Every seeded name is `uniq()`'d.** This database is never truncated
  between runs, so anything a spec later searches for by name must be unique
  per run, or it matches previous runs' leftovers and the assertions drift.
  The pagination spec depends on this directly (exact page counts).
- **Seed through the real HTTP API**, not SQL — the same endpoints the UI
  itself calls, so seeding can't drift from real backend behavior.
- **Reach screens the way staff do.** `ConsultationWorkspace` has no URL of
  its own (`AdminApp.tsx` keeps navigation in React state), so its specs open
  it through the global search bar, exactly as `goToSearchResult` does.
