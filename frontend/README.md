# Patient Web Frontend

React + TypeScript + Vite, built in WEB P3. Talks only to the FastAPI
backend's REST API (`/api/...`) -- never to Postgres directly.

## Local development

1. Start the backend first (see the repo root `SETUP.md`), listening on
   `127.0.0.1:8000` (the default for `uvicorn app.main:app`).
2. `npm install`
3. `npm run dev` -- serves on `127.0.0.1:5173` by default. Requests to
   `/api/*` are proxied to the backend (see `vite.config.ts`), so the
   browser only ever sees one origin in development; no CORS
   configuration exists yet (that's a WEB P10 topic).

## Structure

- `src/api.ts` -- the only place that calls `fetch`. Stores the Bearer
  session token (WEB P2) in `localStorage`.
- `src/format.ts` -- date/time formatting that reads the wall-clock
  digits straight out of the backend's ISO datetime strings, deliberately
  never through a `Date` object's local-timezone conversion. See its
  comments for why: the backend always returns times in the doctor's own
  local time, and this must never be silently reinterpreted in the
  viewer's browser timezone.
- `src/LoginFlow.tsx` -- mobile number -> OTP -> login/registration.
- `src/BookingFlow.tsx` -- department -> doctor -> appointment type ->
  date -> slot -> review -> confirmation. Step order matches
  `app/api/booking.py`'s actual WhatsApp flow order, not the literal
  order in the original WEB P3 prompt text -- see the comment at the top
  of that file and `docs/WEB_P3_PATIENT_BOOKING_UI.md` for why.
- `src/Calendar.tsx` -- month grid backed by `GET /api/web/calendar`.

## Testing

No frontend unit-test framework is set up yet (out of this phase's
scope). The golden path and edge cases (unavailable-date styling, the
booking-window boundary, session expiry) were verified with a real
Chromium browser via Playwright during development -- see
`docs/WEB_P3_PATIENT_BOOKING_UI.md` for what was checked.
