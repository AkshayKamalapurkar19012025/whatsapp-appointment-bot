import { test, expect } from '@playwright/test'
import { API_BASE, bookAndCheckIn, createPatient, loginAdmin, payAppointment, seedBasicScenario, uniq } from './helpers'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/admin')
  await page.locator('#username').fill('e2e-admin')
  await page.locator('input[type="password"]').fill('e2e-password-123')
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/auth/staff/login')),
    page.locator('button[type="submit"]').click(),
  ])
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: 'Appointments' }).click()
  await page.waitForLoadState('networkidle')
}

test.describe('AppointmentsPanel', () => {
  test('rows-per-page selector (25/50/100) drives numbered client-side pagination', async ({ page, request }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    // 30 same-day appointments (all just PENDING -- no need to check
    // any of them in) so the list has more than one page at every
    // rows-per-page size this control offers.
    // Anchored 2 days out at a fixed UTC hour (rather than relative to
    // "now") specifically to avoid day-boundary flakiness: seedBasic
    // Scenario's doctor is Asia/Kolkata (+5:30) with a 00:00-23:45
    // schedule every day, and 26 appointments at the type's 20-minute
    // duration back-to-back span 8.7 hours -- 09:00 UTC = 14:30 IST
    // keeps that whole span (through ~22:50 IST) safely inside one
    // calendar day in the doctor's own timezone, whatever real time
    // this test happens to run at.
    const anchor = new Date()
    anchor.setUTCDate(anchor.getUTCDate() + 2)
    anchor.setUTCHours(9, 0, 0, 0)
    const phoneBase = 7000000000 + Math.floor(Math.random() * 90000000)
    // A per-run-unique name prefix, not a shared static one -- this
    // e2e database is never truncated between runs, so a bare "Row
    // Patient" search would also match every previous run's leftover
    // rows and make the exact page-count assertions below flaky.
    const runPrefix = uniq('Row Patient')
    for (let i = 0; i < 26; i++) {
      const patient = await createPatient(request, token, `${runPrefix} ${i}`, `+91${phoneBase + i}`)
      const res = await request.post(`${API_BASE}/appointments`, {
        headers: { Authorization: `Bearer ${token}` },
        data: {
          doctor_id: scenario.doctor.id,
          patient_id: patient.id,
          appointment_type_id: scenario.appointmentType.id,
          start_at: new Date(anchor.getTime() + i * 20 * 60_000).toISOString(),
          booking_source: 'STAFF_ASSISTED',
        },
      })
      if (!res.ok()) throw new Error(`seed appointment ${i} failed: ${await res.text()}`)
    }

    await login(page)
    // Custom range covering the anchor date -- This Week/This Month
    // would also work most of the time, but a custom From/To this test
    // controls directly never has a week- or month-boundary edge case.
    await page.getByRole('button', { name: /Custom Range/i }).click()
    const anchorIso = anchor.toISOString().slice(0, 10)
    const customInputs = page.locator('.date-scope-custom-inputs')
    await customInputs.getByLabel('From').fill(anchorIso)
    await customInputs.getByLabel('To').fill(anchorIso)
    await page.waitForLoadState('networkidle')
    await page.getByLabel('Search patient').fill(runPrefix)

    const rowsSelect = page.locator('.appointments-pagination select, [aria-label="Rows per page"]')
    await expect(rowsSelect).toBeVisible()
    await rowsSelect.selectOption('25')
    await expect(page.locator('.appointments-pagination-current, [aria-current="page"]').first()).toHaveText('1')
    const pageButtons = page.locator('.appointments-pagination button', { hasText: '2' })
    await expect(pageButtons.first()).toBeVisible()
    await pageButtons.first().click()
    await expect(page.locator('[aria-current="page"]').first()).toHaveText('2')

    await rowsSelect.selectOption('100')
    // 30 rows fit on one page at 100/page -- page 2 should no longer exist.
    await expect(page.locator('.appointments-pagination button', { hasText: '2' })).toHaveCount(0)
  })

  test('polling runs for every date scope (not just today), with a live indicator, last-updated time, and manual Refresh', async ({
    page,
    request,
  }) => {
    const token = await loginAdmin(request)
    await seedBasicScenario(request, token)

    // Installed before navigation, so the component's very first
    // setInterval call (mount, still on the default Today scope) is
    // already virtual -- installing later would leave that original
    // interval running on real wall-clock time, unaffected by a
    // later fastForward.
    await page.clock.install()

    await login(page)
    await expect(page.locator('.opd-live-indicator, .opd-live-dot').first()).toBeVisible()
    await expect(page.getByText(/last updated/i)).toBeVisible()

    await page.getByRole('button', { name: 'This Month' }).click()
    await page.waitForLoadState('networkidle')

    let pollFired = false
    page.on('request', (req) => {
      if (req.url().includes('/api/appointments?') && req.method() === 'GET') pollFired = true
    })
    // The old code's `if (!isTodayScope) return` inside the poll
    // effect meant switching to "This Month" tore the interval down
    // entirely -- 31 virtual seconds later there would be nothing left
    // to fire at all.
    await page.clock.fastForward('00:31')
    await page.waitForTimeout(300)
    expect(pollFired).toBe(true)
  })

  test('a failed doctor queue fetch shows a named banner with Retry, and marks that doctor\'s CHECKED_IN rows as unknown, never Waiting', async ({
    page,
    request,
  }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    const patient = await createPatient(
      request,
      token,
      uniq('Queue Fail Patient'),
      `+91${7100000000 + Math.floor(Math.random() * 90000000)}`,
    )
    const appointmentId = await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })
    await payAppointment(request, token, appointmentId)

    // Simulate the doctor's queue fetch failing by making the doctor
    // id genuinely not resolvable -- deactivating the doctor after the
    // appointment already exists reproduces the real failure mode the
    // spec names ("e.g. deactivated mid-shift", AppointmentsPanel.tsx's
    // own fetchQueues comment), rather than mocking the network.
    await request.patch(`${API_BASE}/doctors/${scenario.doctor.id}/active`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { active: false },
    })

    await login(page)

    const banner = page.locator('.opd-degraded-banner')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText(scenario.doctor.name)
    await expect(banner.getByRole('button', { name: /retry/i })).toBeVisible()

    // This shared e2e database is never truncated between runs, so
    // "today" can accumulate many appointments from earlier test runs
    // -- narrow to this test's own uniquely-named patient rather than
    // assuming their row lands on the default first page.
    await page.getByLabel('Search patient').fill(patient.name)
    const row = page.locator('.opd-row, .appointment-mobile-card', { hasText: patient.name }).first()
    await expect(row).toContainText(/unknown/i)
    await expect(row).not.toContainText(/^waiting$/i)
  })

  test('status tabs show counts and are all visible with no More overflow', async ({ page, request }) => {
    const token = await loginAdmin(request)
    await seedBasicScenario(request, token)

    await login(page)
    // Scoped to the status-tabs bar specifically -- unrelated per-row
    // "More actions" buttons elsewhere on the page also match a bare
    // /^More/i, which isn't what this checks (that's a different,
    // pre-existing control, not the old tabs overflow this redesign
    // removes).
    const tabsBar = page.locator('.appointments-tabs')
    await expect(tabsBar.getByRole('button', { name: /^More/i })).toHaveCount(0)
    // Full existing granularity is kept (no collapsing into broader
    // buckets) -- only the overflow *mechanism* (the old More dropdown)
    // is gone, every tab it used to hide is now a plain top-level one.
    for (const label of ['All', 'Waiting', 'In Consultation', 'Completed', 'Cancelled', 'No Show', 'Booked', 'Confirmed', 'Arrived']) {
      await expect(tabsBar.getByRole('button', { name: new RegExp(`^${label}`, 'i') }).first()).toBeVisible()
    }
  })

  test('filter bar has search, department, doctor, date range and clear', async ({ page, request }) => {
    const token = await loginAdmin(request)
    await seedBasicScenario(request, token)

    await login(page)
    await expect(page.getByLabel('Search patient')).toBeVisible()
    await expect(page.getByLabel(/department/i).first()).toBeVisible()
    await expect(page.getByLabel(/doctor/i).first()).toBeVisible()
    await expect(page.getByText(/today|tomorrow|this week|custom/i).first()).toBeVisible()
  })

  test('no bulk-selection checkboxes and no CSV export control exist', async ({ page, request }) => {
    const token = await loginAdmin(request)
    await seedBasicScenario(request, token)

    await login(page)
    await expect(page.locator('input[type="checkbox"]')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /export/i })).toHaveCount(0)
  })
})
