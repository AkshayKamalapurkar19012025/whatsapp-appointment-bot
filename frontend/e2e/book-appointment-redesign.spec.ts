import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import {
  API_BASE,
  assignAppointmentType,
  assignDoctorToDepartment,
  createAppointmentType,
  createDepartment,
  createDoctor,
  createPatient,
  loginAdmin,
  setClinicHours,
  uniq,
} from './helpers'

// The Book Appointment redesign (soonest-first doctor list, next-
// available card, keyboard-complete booking). Driven against the real
// admin UI and a real backend, like every other spec here.
//
// The seeded department deliberately holds three doctors with
// *different* next-free times plus one whose only slot is already
// taken, because almost every assertion below is about ordering,
// derived timing, or the fully-booked collapse -- none of which a
// single-doctor fixture can express.

const VIEWPORTS = [
  { width: 1440, height: 1000 },
  { width: 1280, height: 1000 },
]

// Seeds a department whose doctors open at staggered times, so
// "soonest first" has something to sort. Each doctor runs a clinic day
// starting at their own hour; the date under test is always Tomorrow,
// so the whole day is ahead regardless of when the suite runs.
async function seedDepartment(request: import('@playwright/test').APIRequestContext) {
  const token = await loginAdmin(request)
  const department = await createDepartment(request, token, uniq('Cardiology'))
  const appointmentType = await createAppointmentType(request, token, uniq('Consultation'))
  const followUp = await createAppointmentType(request, token, uniq('Follow-up'))

  // Opens later, so must sort SECOND despite being created first.
  const late = await createDoctor(request, token, uniq('Dr Late'), 'Cardiology')
  await assignDoctorToDepartment(request, token, late.id, department.id)
  await assignAppointmentType(request, token, late.id, appointmentType.id, {
    duration_minutes: 35,
    consultation_fee: 280,
  })
  await assignAppointmentType(request, token, late.id, followUp.id, {
    duration_minutes: 20,
    consultation_fee: 150,
  })
  await setClinicHours(request, token, late.id, '11:00', '17:00')

  // Opens earliest, so must sort FIRST.
  const early = await createDoctor(request, token, uniq('Dr Early'), 'Cardiology')
  await assignDoctorToDepartment(request, token, early.id, department.id)
  await assignAppointmentType(request, token, early.id, appointmentType.id, {
    duration_minutes: 35,
    consultation_fee: 300,
  })
  await assignAppointmentType(request, token, early.id, followUp.id, {
    duration_minutes: 20,
    consultation_fee: 180,
  })
  await setClinicHours(request, token, early.id, '09:00', '17:00')

  // Exactly one 35-minute slot in the day, which the test books out --
  // total_slots stays 1 while slots goes empty, which is what
  // distinguishes "fully booked" from "not working today".
  const booked = await createDoctor(request, token, uniq('Dr Fullybooked'), 'Cardiology')
  await assignDoctorToDepartment(request, token, booked.id, department.id)
  await assignAppointmentType(request, token, booked.id, appointmentType.id, {
    duration_minutes: 35,
    consultation_fee: 280,
  })
  await setClinicHours(request, token, booked.id, '09:00', '09:35')

  const patient = await createPatient(
    request,
    token,
    uniq('Ramesh Kulkarni'),
    `+91${Math.floor(7000000000 + Math.random() * 999999999)}`,
    { date_of_birth: '1971-06-14', gender: 'MALE' },
  )

  return { token, department, appointmentType, followUp, early, late, booked, patient }
}

function isoTomorrow(): string {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// Books out Dr Fullybooked's single slot via the API, so the UI sees a
// doctor with capacity but nothing left.
async function fillTheOneSlot(
  request: import('@playwright/test').APIRequestContext,
  scenario: Awaited<ReturnType<typeof seedDepartment>>,
) {
  const date = isoTomorrow()
  const avail = await request.get(
    `${API_BASE}/appointments/availability/by-department?department_id=${scenario.department.id}` +
      `&appointment_type_id=${scenario.appointmentType.id}&selected_date=${date}`,
    { headers: { Authorization: `Bearer ${scenario.token}` } },
  )
  const body = await avail.json()
  const target = body.doctors.find((d: { id: number }) => d.id === scenario.booked.id)
  if (!target || target.slots.length === 0) throw new Error('expected Dr Fullybooked to have one free slot to fill')
  const res = await request.post(`${API_BASE}/appointments`, {
    headers: { Authorization: `Bearer ${scenario.token}` },
    data: {
      doctor_id: scenario.booked.id,
      patient_id: scenario.patient.id,
      appointment_type_id: scenario.appointmentType.id,
      start_at: target.slots[0].start_at,
      booking_source: 'STAFF_ASSISTED',
    },
  })
  if (!res.ok()) throw new Error(`could not fill the slot: ${await res.text()}`)
}

async function openPanel(page: Page) {
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
  await page.getByRole('button', { name: /New OPD Visit/i }).first().click()
  await page.getByText('Book Appointment', { exact: true }).first().click()
  await page.waitForLoadState('networkidle')
}

// Drives the panel to "patient chosen, department chosen, type chosen,
// doctors loaded for Tomorrow".
async function readyToPick(
  page: Page,
  scenario: Awaited<ReturnType<typeof seedDepartment>>,
  opts: { source?: 'Walk-in' | 'Phone' | 'Online' | 'Staff-assisted' } = {},
) {
  await openPanel(page)
  if (opts.source && opts.source !== 'Walk-in') {
    await page.getByRole('radio', { name: new RegExp(opts.source, 'i') }).click()
  }
  await page.locator('.book-patient-search input').fill(scenario.patient.name)
  await page.locator('.book-patient-result').first().click()
  await expect(page.locator('.book-selected-patient-card')).toBeVisible()
  await page.locator('.book-dept-pills').getByRole('button', { name: scenario.department.name }).click()
  await page.getByRole('radio', { name: scenario.appointmentType.name }).click()
  if (opts.source && opts.source !== 'Walk-in') {
    await page.getByRole('button', { name: 'Tomorrow', exact: true }).click()
  }
  await page.waitForLoadState('networkidle')
}

test.describe('BookAppointmentPanel redesign', () => {
  // --- Walk-in locks the date to today -------------------------------
  test('walk-in locks the date to today: no Tomorrow, no date picker', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario)

    // Walk-in: the date is stated, not chosen.
    await expect(page.getByRole('button', { name: 'Tomorrow', exact: true })).toHaveCount(0)
    await expect(page.locator('input[aria-label="Jump to date"]')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Previous day' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Next day' })).toHaveCount(0)
    await expect(page.locator('.book-date-locked')).toContainText(/Today/)
    // Today/Tomorrow supplies the comma; the weekday must not add a
    // second one ("Today, Thu, 1 Oct 2026").
    await expect(page.locator('.book-date-locked')).not.toContainText(/Today, \w+,/)

    // Any other source gets the picker back.
    await page.getByRole('radio', { name: /Phone/i }).click()
    await expect(page.getByRole('button', { name: 'Tomorrow', exact: true })).toHaveCount(1)
    await expect(page.locator('input[aria-label="Jump to date"]')).toHaveCount(1)
  })

  // --- Next available card -------------------------------------------
  test('next-available card names the soonest doctor and its time', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    // Leaves exactly two bookable doctors, so "soonest" and "runner-up"
    // are Dr Early and Dr Late and nothing else can sit between them.
    await fillTheOneSlot(request, scenario)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario, { source: 'Phone' })

    const card = page.locator('.book-next-available')
    await expect(card).toBeVisible()
    // Dr Early opens at 09:00, Dr Late at 11:00 -- the card must name
    // the earlier one, never whichever the API happened to return first.
    await expect(card).toContainText(scenario.early.name)
    await expect(card).toContainText('9:00 AM')
    await expect(card.locator('.book-next-available-doctor')).not.toContainText(scenario.late.name)
    // The runner-up is offered too.
    await expect(page.locator('.book-next-available-runner-up')).toContainText(scenario.late.name)
    await expect(page.locator('.book-next-available-runner-up')).toContainText('11:00 AM')
  })

  // "Minutes away" is only meaningful for the current clinic date. On a
  // future date it is a four-figure number nobody reads, so it is
  // suppressed there and the date label carries the distance instead --
  // which means walk-in, the today-locked source, is where it shows.
  test('how far off the next slot is shows for today and is suppressed for a future date', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])

    await readyToPick(page, scenario)
    const todayCard = page.locator('.book-next-available')
    if (await todayCard.count()) {
      await expect(todayCard.locator('.book-next-available-eta')).toHaveText(/in \d+ (min|hr)|now/)
    }

    await page.getByRole('radio', { name: /Phone/i }).click()
    await page.getByRole('button', { name: 'Tomorrow', exact: true }).click()
    await page.waitForLoadState('networkidle')
    await expect(page.locator('.book-next-available')).toBeVisible()
    await expect(page.locator('.book-next-available-eta')).toHaveCount(0)
  })

  test('Enter books the next-available slot when nothing else is selected', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario, { source: 'Phone' })

    await expect(page.locator('.book-grid-slot.selected')).toHaveCount(0)
    // Enter on a focused button activates that button, which is correct
    // -- so the accelerator is tested from a clean focus state, the way
    // it is reached in practice.
    await page.keyboard.press('Escape')
    await page.keyboard.press('Enter')
    await expect(page.locator('.opd-success')).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('.opd-success')).toContainText(scenario.early.name)
    await expect(page.locator('.opd-success')).toContainText('9:00 AM')
  })

  // --- Doctor rows ----------------------------------------------------
  test('doctor rows sort soonest-first and state slots left and next free time', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await fillTheOneSlot(request, scenario)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario, { source: 'Phone' })

    const rows = page.locator('.book-doctor-row')
    await expect(rows).toHaveCount(2) // the fully-booked one collapses elsewhere
    // Dr Early (09:00) before Dr Late (11:00).
    await expect(rows.nth(0)).toContainText(scenario.early.name)
    await expect(rows.nth(1)).toContainText(scenario.late.name)

    // Each row states its own next free time and how many are left.
    await expect(rows.nth(0).locator('.book-doctor-next')).toContainText('9:00 AM')
    await expect(rows.nth(1).locator('.book-doctor-next')).toContainText('11:00 AM')
    await expect(rows.nth(0).locator('.book-doctor-left')).toHaveText(/\d+ left/)

    // Slots are time-labelled chips, in order, starting at the
    // doctor's own first free time.
    await expect(rows.nth(0).locator('.book-grid-slot').first()).toHaveText('9:00 AM')
    await expect(rows.nth(1).locator('.book-grid-slot').first()).toHaveText('11:00 AM')

    // The header counts only the doctors who can actually be booked.
    await expect(page.locator('.book-doctor-list-header')).toContainText('2 doctors available')
    await expect(page.locator('.book-doctor-list-header')).toContainText('soonest first')
  })

  test('a fully-booked doctor collapses to a single line, distinct from one not working', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await fillTheOneSlot(request, scenario)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario, { source: 'Phone' })

    const collapsed = page.locator('.book-doctor-collapsed')
    await expect(collapsed).toHaveCount(1)
    await expect(collapsed).toContainText(scenario.booked.name)
    // Capacity existed and is spent -- not the same as having no clinic.
    await expect(collapsed).toContainText(/fully booked/i)
    // One line: no slot chips, and no expanded row for this doctor.
    await expect(collapsed.locator('.book-grid-slot')).toHaveCount(0)
    await expect(page.locator('.book-doctor-row', { hasText: scenario.booked.name })).toHaveCount(0)
  })

  // --- Type before slots ----------------------------------------------
  test('no slots render until a visit type is chosen, and switching type re-sizes them', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openPanel(page)
    await page.getByRole('radio', { name: /Phone/i }).click()
    await page.locator('.book-patient-search input').fill(scenario.patient.name)
    await page.locator('.book-patient-result').first().click()
    await page.locator('.book-dept-pills').getByRole('button', { name: scenario.department.name }).click()
    await page.waitForLoadState('networkidle')

    // Department chosen, type not: duration is per-doctor-per-type, so
    // there is nothing honest to draw yet.
    await expect(page.locator('.book-grid-slot')).toHaveCount(0)
    await expect(page.locator('.book-doctor-row')).toHaveCount(0)
    await expect(page.locator('.book-type-required')).toBeVisible()

    await page.getByRole('radio', { name: scenario.appointmentType.name }).click()
    await page.getByRole('button', { name: 'Tomorrow', exact: true }).click()
    await page.waitForLoadState('networkidle')
    await expect(page.locator('.book-grid-slot').first()).toBeVisible()

    // 35-minute Consultation: 9:00, 9:35, 10:10 ...
    const firstRow = page.locator('.book-doctor-row').first()
    await expect(firstRow.locator('.book-grid-slot').nth(1)).toHaveText('9:35 AM')

    // 20-minute Follow-up on the same doctor: 9:00, 9:20, 9:40 ...
    await page.getByRole('radio', { name: scenario.followUp.name }).click()
    await page.waitForLoadState('networkidle')
    await expect(page.locator('.book-doctor-row').first().locator('.book-grid-slot').nth(1)).toHaveText('9:20 AM')
  })

  // --- Removals --------------------------------------------------------
  test('step numbers and the doctor time-zone line are gone', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario, { source: 'Phone' })

    await expect(page.locator('.book-step-number')).toHaveCount(0)
    await expect(page.locator('.book-appointment-page')).not.toContainText(/Doctor's time zone/i)
  })

  // --- Summary ---------------------------------------------------------
  test('summary states patient, doctor, time, duration and fee, and the button states the action', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario, { source: 'Phone' })
    await page.locator('.book-doctor-row').first().locator('.book-grid-slot').first().click()

    const summary = page.locator('.book-review-card')
    await expect(summary.locator('.book-summary-patient')).toContainText(scenario.patient.name)
    await expect(summary.locator('.book-summary-patient')).toContainText(scenario.patient.uhid)
    await expect(summary.locator('.book-summary-doctor')).toContainText(scenario.early.name)
    await expect(summary.locator('.book-summary-when')).toContainText('9:00 AM')
    // Duration and fee are the per-doctor-per-type assignment's real
    // values (Dr Early's Consultation is 35 min / Rs 300).
    await expect(summary.locator('.book-summary-when')).toContainText('35 min')
    await expect(summary.locator('.book-summary-fee')).toContainText('300')

    // The primary button says what it will do, not just "Book".
    await expect(page.locator('.book-review-cta')).toHaveText(/Book appointment/i)
  })

  test('walk-in summary offers check-in, and its button says so', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario)
    const firstChip = page.locator('.book-doctor-row').first().locator('.book-grid-slot').first()
    if (await firstChip.count()) await firstChip.click()

    await expect(page.locator('.book-checkin-toggle')).toBeVisible()
    await expect(page.locator('.book-review-cta')).toHaveText(/Book . check in/i)
  })

  // --- Keyboard ---------------------------------------------------------
  test('keyboard: / focuses search, W/P/O/S switch source, Esc clears', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openPanel(page)

    await page.keyboard.press('/')
    await expect(page.locator('.book-patient-search input')).toBeFocused()

    // Letters typed into the search box must NOT switch source.
    await page.keyboard.type('Walk')
    await expect(page.locator('.book-patient-search input')).toHaveValue('Walk')
    await expect(page.getByRole('radio', { name: /Walk-in/i })).toHaveAttribute('aria-checked', 'true')

    // Esc clears the field and takes focus out of it...
    await page.keyboard.press('Escape')
    await expect(page.locator('.book-patient-search input')).toHaveValue('')

    // ...after which the source keys work.
    await page.keyboard.press('p')
    await expect(page.getByRole('radio', { name: /Phone/i })).toHaveAttribute('aria-checked', 'true')
    await page.keyboard.press('o')
    await expect(page.getByRole('radio', { name: /Online/i })).toHaveAttribute('aria-checked', 'true')
    await page.keyboard.press('s')
    await expect(page.getByRole('radio', { name: /Staff-assisted/i })).toHaveAttribute('aria-checked', 'true')
    await page.keyboard.press('w')
    await expect(page.getByRole('radio', { name: /Walk-in/i })).toHaveAttribute('aria-checked', 'true')
  })

  test('a whole booking can be completed without a mouse', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openPanel(page)
    await page.getByRole('radio', { name: /Phone/i }).click()

    // Patient, by keyboard.
    await page.keyboard.press('/')
    await page.keyboard.type(scenario.patient.name)
    await page.waitForTimeout(600)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    await expect(page.locator('.book-selected-patient-card')).toBeVisible()

    // Department + type + date still need selecting; this spec is about
    // the slot grid and booking being reachable by key alone.
    await page.locator('.book-dept-pills').getByRole('button', { name: scenario.department.name }).click()
    await page.getByRole('radio', { name: scenario.appointmentType.name }).click()
    await page.getByRole('button', { name: 'Tomorrow', exact: true }).click()
    await page.waitForLoadState('networkidle')

    // Wait for THIS date's chips before focusing one: a slot's React
    // key carries its full ISO start time, so changing the date
    // remounts every chip, and focus taken before the new data lands is
    // dropped with the old nodes.
    const firstChip = page.locator('.book-doctor-row').first().locator('.book-grid-slot').first()
    await expect(firstChip).toHaveText('9:00 AM')

    // Arrows move between slots, Enter books.
    await firstChip.focus()
    await page.keyboard.press('ArrowRight')
    await expect(page.locator('.book-grid-slot:focus')).toHaveText('9:35 AM')
    await page.keyboard.press('Enter')
    await expect(page.locator('.book-grid-slot.selected')).toHaveText('9:35 AM')
    await page.keyboard.press('Enter')
    await expect(page.locator('.opd-success')).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('.opd-success')).toContainText('9:35 AM')
  })

  // --- Preserved capabilities --------------------------------------------
  test('booking source is still persisted on the created appointment', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await readyToPick(page, scenario, { source: 'Staff-assisted' })
    await page.locator('.book-doctor-row').first().locator('.book-grid-slot').first().click()
    await page.locator('.book-review-cta').click()
    await expect(page.locator('.opd-success')).toBeVisible({ timeout: 15_000 })

    const list = await request.get(
      `${API_BASE}/appointments?doctor_id=${scenario.early.id}&date_from=${isoTomorrow()}&date_to=${isoTomorrow()}`,
      { headers: { Authorization: `Bearer ${scenario.token}` } },
    )
    const body = await list.json()
    const rows = Array.isArray(body) ? body : body.items
    expect(rows.some((a: { booking_source?: string }) => a.booking_source === 'STAFF_ASSISTED')).toBe(true)
  })

  test('register and edit patient are both still reachable', async ({ page, request }) => {
    const scenario = await seedDepartment(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openPanel(page)

    await page.getByRole('button', { name: /Register new patient/i }).click()
    await expect(page.locator('.modal-panel')).toContainText(/Register new patient/i)
    await page.locator('.modal-close').click()
    await expect(page.locator('.modal-overlay')).toHaveCount(0)

    await page.locator('.book-patient-search input').fill(scenario.patient.name)
    await page.locator('.book-patient-result').first().click()
    await page.getByRole('button', { name: 'Edit', exact: true }).click()
    await expect(page.locator('.modal-panel')).toContainText(/Verify . edit patient details/i)
  })

  test('walk-in check-in, free-visit auto-settle and token printing all survive', async ({ page, request }) => {
    const token = await loginAdmin(request)
    const department = await createDepartment(request, token, uniq('Free Dept'))
    const appointmentType = await createAppointmentType(request, token, uniq('Free Consult'))
    const doctor = await createDoctor(request, token, uniq('Dr Freebie'), 'General Medicine')
    await assignDoctorToDepartment(request, token, doctor.id, department.id)
    // Fee 0 is what routes a walk-in through settle_free_visit on check-in.
    await assignAppointmentType(request, token, doctor.id, appointmentType.id, {
      duration_minutes: 30,
      consultation_fee: 0,
    })
    await setClinicHours(request, token, doctor.id, '00:00', '23:45')
    const patient = await createPatient(
      request,
      token,
      uniq('Free Patient'),
      `+91${Math.floor(7000000000 + Math.random() * 999999999)}`,
    )

    await page.setViewportSize(VIEWPORTS[0])
    await openPanel(page)
    await page.locator('.book-patient-search input').fill(patient.name)
    await page.locator('.book-patient-result').first().click()
    await page.locator('.book-dept-pills').getByRole('button', { name: department.name }).click()
    await page.getByRole('radio', { name: appointmentType.name }).click()
    await page.waitForLoadState('networkidle')
    await page.locator('.book-grid-slot').first().click()
    await page.locator('.book-review-cta').click()
    await expect(page.locator('.opd-success')).toBeVisible({ timeout: 20_000 })

    // Walk-in + zero fee => checked in and auto-settled, so a real
    // queue token exists and the printable slip is rendered.
    await expect(page.locator('.opd-success')).toContainText(/already in the queue|Checked in/i)
    await expect(page.locator('.token-slip')).toHaveCount(1)
    await expect(page.getByRole('button', { name: /Print Token/i })).toBeVisible()
  })
})
