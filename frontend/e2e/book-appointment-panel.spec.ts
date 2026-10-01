import { test, expect } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
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

// Layout regressions on the single-screen scheduler (BookAppointment
// Panel.tsx). Every assertion here is about *rendered geometry* -- a
// chip's measured width, whether two elements share a row, whether a
// scroll container is wider than its own viewport -- which is exactly
// the class of bug a DOM-only or mocked test cannot see: every one of
// these defects shipped with the correct markup and the wrong boxes.
//
// The seeded doctor runs a real 09:00-17:00 clinic day at a 35-minute
// appointment duration (13 slots: 9:00 AM through 4:20 PM), not
// helpers.ts's 00:00-23:45 setFullAvailability -- the grid specs below
// assert that the *whole* working day is reachable, which a 24-hour
// schedule can't express.

const VIEWPORTS = [
  { width: 1440, height: 1000 },
  { width: 1280, height: 1000 },
]

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

// Every slot start the seeded 09:00-17:00 / 35-minute doctor has, in
// order -- 9:00, 9:35, 10:10 ... 4:20 PM. Written out rather than
// hardcoded as a literal list so the arithmetic stays visible.
function expectedSlotLabels(): string[] {
  const labels: string[] = []
  for (let m = 9 * 60; m + 35 <= 17 * 60; m += 35) {
    const h24 = Math.floor(m / 60)
    const suffix = h24 >= 12 ? 'PM' : 'AM'
    const h = h24 % 12 === 0 ? 12 : h24 % 12
    labels.push(`${h}:${String(m % 60).padStart(2, '0')} ${suffix}`)
  }
  return labels
}

async function seedScenario(request: import('@playwright/test').APIRequestContext) {
  const token = await loginAdmin(request)
  const department = await createDepartment(request, token, uniq('Cardiology'))
  const appointmentType = await createAppointmentType(request, token, uniq('Consultation'))
  const doctor = await createDoctor(request, token, uniq('Dr Amogh Purohit'), 'Sr Specialist')
  await assignDoctorToDepartment(request, token, doctor.id, department.id)
  await assignAppointmentType(request, token, doctor.id, appointmentType.id, {
    duration_minutes: 35,
    consultation_fee: 280,
  })
  await setClinicHours(request, token, doctor.id)
  const patient = await createPatient(
    request,
    token,
    uniq('Ashok Rane'),
    `+91${Math.floor(7000000000 + Math.random() * 999999999)}`,
    { date_of_birth: '1999-09-01', gender: 'MALE' },
  )
  return { token, department, appointmentType, doctor, patient }
}

// Reaches Book Appointment the way reception does (OPD Today's "New
// OPD Visit" menu -- AdminApp.tsx keeps navigation in React state, so
// there is no URL to go to), then drives it to the state every
// assertion below needs: a selected patient, a chosen appointment
// type, and Tomorrow's grid loaded.
async function openBookAppointment(
  page: Page,
  scenario: Awaited<ReturnType<typeof seedScenario>>,
  opts: { pickSlot?: boolean; expectSlots?: boolean } = {},
) {
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

  await page.getByLabel(/Search by name, mobile number, or UHID/i).fill(scenario.patient.name)
  await page.locator('.book-patient-result').first().click()
  await expect(page.locator('.book-selected-patient-card')).toBeVisible()

  // This run's own department/type, never whichever one happens to
  // sort first -- this e2e database is never truncated, so previous
  // runs' departments are all still present and the panel defaults to
  // the first one.
  await page.locator('.book-scheduler-filter-group').getByRole('button', { name: scenario.department.name }).click()
  await page.getByRole('radio', { name: scenario.appointmentType.name }).click()
  await page.getByRole('button', { name: 'Tomorrow', exact: true }).click()
  await page.waitForLoadState('networkidle')
  if (opts.expectSlots === false) {
    await expect(page.locator('.book-grid-row').first()).toBeVisible()
  } else {
    await expect(page.locator('.book-grid-slot').first()).toBeVisible()
  }

  if (opts.pickSlot) {
    await page.locator('.book-grid-slot').first().click()
    await expect(page.locator('.book-grid-slot.selected')).toHaveCount(1)
  }
}

async function box(locator: Locator) {
  const b = await locator.boundingBox()
  if (!b) throw new Error('element has no bounding box')
  return b
}

// WCAG relative luminance, used by fix 6's "differentiate by lightness,
// not hue alone" assertion -- two colors that differ only in hue have
// (near) identical luminance, which is exactly the failure being
// tested, so comparing the channels directly would not catch it.
function luminance(rgb: string): number {
  const m = rgb.match(/(\d+(?:\.\d+)?)/g)
  if (!m) throw new Error(`unparseable color: ${rgb}`)
  const [r, g, b] = m.slice(0, 3).map((v) => {
    const c = Number(v) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function channels(rgb: string): [number, number, number] {
  const m = rgb.match(/(\d+(?:\.\d+)?)/g)
  if (!m) throw new Error(`unparseable color: ${rgb}`)
  return [Number(m[0]), Number(m[1]), Number(m[2])]
}

test.describe('BookAppointmentPanel layout', () => {
  // Fix 1 -- the selected-patient card. The bug: .book-selected-
  // patient-header wraps, dropping "Edit Change" onto its own row
  // below "SELECTED", and .book-selected-patient-body centers a 36px
  // avatar against a four-line text block so the initials float
  // mid-card with the name pushed off to the right.
  test('selected patient card: avatar and name share a row, Edit/Change sit in the header', async ({ page, request }) => {
    const scenario = await seedScenario(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openBookAppointment(page, scenario)

    const card = page.locator('.book-selected-patient-card')
    const selectedLabel = card.getByText('Selected', { exact: true })
    const edit = card.getByRole('button', { name: 'Edit' })
    const change = card.getByRole('button', { name: 'Change' })
    const avatar = card.locator('.book-patient-avatar')
    const name = card.locator('.book-selected-patient-name')
    const identifiers = card.locator('.book-selected-patient-meta').first()

    // Edit/Change are on the SAME row as "Selected", right-aligned --
    // not a bare row of their own above the patient's name.
    const labelBox = await box(selectedLabel)
    const editBox = await box(edit)
    const changeBox = await box(change)
    expect(Math.abs(editBox.y + editBox.height / 2 - (labelBox.y + labelBox.height / 2))).toBeLessThan(6)
    expect(Math.abs(changeBox.y + changeBox.height / 2 - (labelBox.y + labelBox.height / 2))).toBeLessThan(6)
    expect(editBox.x).toBeGreaterThan(labelBox.x + labelBox.width)
    // Keeping them on one row must not be paid for by clipping the
    // badge: the header has to be wide enough for both at once.
    const badgeClip = await selectedLabel.evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(badgeClip).toBeLessThanOrEqual(1)
    await expect(selectedLabel.locator('svg')).toBeVisible()
    // Right-aligned: "Change" is the last thing in the header row.
    const cardBox = await box(card)
    expect(changeBox.x + changeBox.width).toBeGreaterThan(cardBox.x + cardBox.width - 32)

    // Avatar and name on one row...
    const avatarBox = await box(avatar)
    const nameBox = await box(name)
    expect(Math.abs(avatarBox.y + avatarBox.height / 2 - (nameBox.y + nameBox.height / 2))).toBeLessThan(8)
    expect(nameBox.x).toBeGreaterThan(avatarBox.x)

    // ...with the identifiers BELOW that row, not beside the avatar.
    const idBox = await box(identifiers)
    expect(idBox.y).toBeGreaterThanOrEqual(avatarBox.y + avatarBox.height - 2)
    // Being below the avatar rather than beside it is what buys the
    // identifiers the card's full inner width -- in the broken layout
    // the avatar and the whole text block were flex siblings, so every
    // line of text shared the same ~150px gutter. This is the
    // measurable difference between the two layouts.
    expect(idBox.width).toBeGreaterThan(nameBox.width + avatarBox.width - 8)
  })

  // Fix 2 -- age alongside DOB. Already shown in the search *results*
  // list (formatPreciseAge), never on the selected card.
  test('selected patient card shows age alongside the date of birth', async ({ page, request }) => {
    const scenario = await seedScenario(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openBookAppointment(page, scenario)

    const meta = await page.locator('.book-selected-patient-card').innerText()
    // DOB 1999-09-01. Age is whatever it is on the day this runs, so
    // it is computed here rather than hardcoded -- the assertion is
    // that DOB, age and gender all appear, in that order.
    const dob = new Date('1999-09-01T00:00:00')
    const now = new Date()
    let age = now.getFullYear() - dob.getFullYear()
    if (now.getMonth() < dob.getMonth() || (now.getMonth() === dob.getMonth() && now.getDate() < dob.getDate())) age -= 1
    expect(meta).toContain(`1 Sep 1999 · ${age} · Male`)
  })

  for (const vp of VIEWPORTS) {
    // Fix 3 -- the grid is clipped: .book-grid-card scrolls its own
    // content (scrollWidth 584 vs clientWidth 360 as measured), so the
    // day both starts scrolled away from 9 AM and runs off the right
    // edge after midday.
    test(`slot grid shows the doctor's whole working day without horizontal clipping at ${vp.width}px`, async ({ page, request }) => {
      const scenario = await seedScenario(request)
      await page.setViewportSize(vp)
      await openBookAppointment(page, scenario)

      const clipping = await page.locator('.book-grid-card').evaluate((el) => ({
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        scrollLeft: el.scrollLeft,
      }))
      expect(clipping.scrollWidth).toBeLessThanOrEqual(clipping.clientWidth + 1)
      expect(clipping.scrollLeft).toBe(0)

      // The whole day is present, in order, starting at the doctor's
      // real first slot and ending at their real last one.
      const labels = expectedSlotLabels()
      await expect(page.locator('.book-grid-slot')).toHaveCount(labels.length)
      await expect(page.locator('.book-grid-slot')).toHaveText(labels)

      // ...and every one of them is actually inside the card's box.
      const cardBox = await box(page.locator('.book-grid-card'))
      const count = await page.locator('.book-grid-slot').count()
      for (let i = 0; i < count; i++) {
        const b = await box(page.locator('.book-grid-slot').nth(i))
        expect(b.x).toBeGreaterThanOrEqual(cardBox.x - 1)
        expect(b.x + b.width).toBeLessThanOrEqual(cardBox.x + cardBox.width + 1)
      }
    })

    // Fix 4 -- the page clips at the viewport with dead space bottom-
    // right: .admin-shell is capped at 1180px, so at 1440px a quarter
    // of the window is empty gutter while the grid inside is starved
    // into a 362px column and scrolls.
    test(`page layout fits the window at ${vp.width}px`, async ({ page, request }) => {
      const scenario = await seedScenario(request)
      await page.setViewportSize(vp)
      await openBookAppointment(page, scenario)

      const doc = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }))
      // No horizontal page scroll...
      expect(doc.scrollWidth).toBeLessThanOrEqual(doc.clientWidth)

      // ...and no large dead gutter either: the shell uses the window
      // it was given rather than sitting as a fixed island in it.
      const shell = await box(page.locator('.admin-shell'))
      expect(shell.width).toBeGreaterThan(vp.width - 80)

      // The date nav stays a single control: the carets that step the
      // day never orphan onto a row of their own away from the date
      // they step.
      // Compared on vertical centers, not tops: the nav's children are
      // center-aligned and genuinely different heights, so equal tops
      // was never the property that means "one row".
      const navCenters = await page
        .locator('.book-date-nav > *')
        .evaluateAll((els) =>
          els.map((el) => {
            const r = el.getBoundingClientRect()
            return r.top + r.height / 2
          }),
        )
      expect(navCenters.length).toBeGreaterThan(1)
      expect(Math.max(...navCenters) - Math.min(...navCenters)).toBeLessThan(8)
    })

    // Fix 5 -- slot pills were unlabelled 26px slivers, sized as a
    // fraction of a fixed *hour* axis rather than by the doctor's own
    // 35-minute slot length.
    test(`each slot renders as a labelled chip at least 44px wide at ${vp.width}px`, async ({ page, request }) => {
      const scenario = await seedScenario(request)
      await page.setViewportSize(vp)
      await openBookAppointment(page, scenario)

      const labels = expectedSlotLabels()
      const slots = page.locator('.book-grid-slot')
      await expect(slots).toHaveCount(labels.length)

      for (let i = 0; i < labels.length; i++) {
        const slot = slots.nth(i)
        await expect(slot).toHaveText(labels[i])
        const b = await box(slot)
        expect(b.width).toBeGreaterThanOrEqual(44)
        // The label is not clipped inside its own chip.
        const overflow = await slot.evaluate((el) => el.scrollWidth - el.clientWidth)
        expect(overflow).toBeLessThanOrEqual(1)
      }

      // Columns are sized off the doctor's real slot length, so a
      // 35-minute slot is wider than the 44px floor a 20-minute one
      // would get.
      const first = await box(slots.first())
      expect(first.width).toBeGreaterThan(44)
    })
  }

  // The "Unavailable" legend entry has to correspond to something the
  // grid can actually draw. It did not: the grid was gated on
  // gridBounds, which is null when NO returned doctor has a slot, so a
  // department whose doctors are all off that day rendered a blank
  // card -- no rows, no names, no markers -- even though the endpoint
  // returns those doctors on purpose (include_unavailable=True).
  test('a department whose doctors have no slots still renders their rows, marked Unavailable', async ({ page, request }) => {
    const token = await loginAdmin(request)
    const department = await createDepartment(request, token, uniq('Closed Dept'))
    const appointmentType = await createAppointmentType(request, token, uniq('Closed Type'))
    const doctor = await createDoctor(request, token, uniq('Dr Offduty'), 'General Medicine')
    await assignDoctorToDepartment(request, token, doctor.id, department.id)
    await assignAppointmentType(request, token, doctor.id, appointmentType.id, {
      duration_minutes: 30,
      consultation_fee: 100,
    })
    // Deliberately no schedule at all, so every date has zero slots.
    const patient = await createPatient(
      request,
      token,
      uniq('Closed Patient'),
      `+91${Math.floor(7000000000 + Math.random() * 999999999)}`,
    )
    // The endpoint really does return the doctor -- this is a
    // rendering assertion, not a backend one.
    const availability = await request.get(
      `${API_BASE}/appointments/availability/by-department?department_id=${department.id}` +
        `&appointment_type_id=${appointmentType.id}&selected_date=${new Date().toISOString().slice(0, 10)}`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    const body = await availability.json()
    expect(body.doctors).toHaveLength(1)
    expect(body.doctors[0].slots).toHaveLength(0)

    await page.setViewportSize(VIEWPORTS[0])
    await openBookAppointment(page, { token, department, appointmentType, doctor, patient }, { expectSlots: false })

    await expect(page.locator('.book-grid-row')).toHaveCount(1)
    await expect(page.locator('.book-grid-row-doctor')).toContainText(doctor.name)
    await expect(page.locator('.book-grid-unavailable')).toHaveCount(1)
  })

  // Fix 6 -- Available and Unavailable were both pale tints
  // (--color-primary-soft vs --color-bg-subtle) separated by hue
  // alone, which is invisible at a 13px swatch and to anyone with a
  // color vision deficiency.
  test('available and unavailable are separated by lightness and border, not hue', async ({ page, request }) => {
    const scenario = await seedScenario(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openBookAppointment(page, scenario)

    const read = (sel: string) =>
      page.locator(sel).first().evaluate((el) => {
        const s = getComputedStyle(el)
        return {
          background: s.backgroundColor,
          borderColor: s.borderTopColor,
          borderStyle: s.borderTopStyle,
          borderWidth: s.borderTopWidth,
        }
      })

    const available = await read('.book-grid-legend-swatch.available')
    const unavailable = await read('.book-grid-legend-swatch.unavailable')

    // Lightness, not hue: a real contrast ratio between the two fills.
    const la = luminance(available.background)
    const lu = luminance(unavailable.background)
    const ratio = (Math.max(la, lu) + 0.05) / (Math.min(la, lu) + 0.05)
    expect(ratio).toBeGreaterThan(1.12)

    // ...and a border that differs too, so the two are still
    // distinguishable in greyscale.
    expect(
      available.borderColor !== unavailable.borderColor || available.borderStyle !== unavailable.borderStyle,
    ).toBe(true)

    // No red: neither swatch may be a red-dominant fill.
    for (const swatch of [available, unavailable]) {
      const [r, g, b] = channels(swatch.background)
      expect(r).toBeLessThanOrEqual(Math.max(g, b) + 4)
    }

    // The same treatment reaches the real chips, not just the legend.
    const chip = await read('.book-grid-slot')
    expect(luminance(chip.background)).toBeCloseTo(la, 2)
  })

  // Fix 7 -- the date nav rendered a raw <input type="date">, whose
  // native "01/10/2026" is ambiguous (and locale-ordered: the same
  // value renders 10/02/2026 under en-US).
  test('dates render unambiguously as "Thu, 1 Oct 2026"', async ({ page, request }) => {
    const scenario = await seedScenario(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openBookAppointment(page, scenario)

    // Built from whatever date the panel is actually on, so this never
    // depends on the day the suite runs.
    const iso = await page.locator('input[aria-label="Jump to date"]').inputValue()
    const [y, m, d] = iso.split('-').map(Number)
    const local = new Date(y, m - 1, d)
    const expected = `${WEEKDAYS[local.getDay()]}, ${d} ${MONTHS[m - 1]} ${y}`

    await expect(page.locator('.book-date-nav')).toContainText(expected)
    // The ambiguous numeric rendering is gone from the visible nav.
    await expect(page.locator('.book-date-nav')).not.toContainText(/\d{2}\/\d{2}\/\d{4}/)

    // The native control still exists -- it is what does the picking,
    // and removing it would take its calendar, its keyboard handling
    // and its accessible name with it -- but it is laid transparently
    // over the label rather than rendering its own locale-ordered
    // digits beside ours. A DOM text assertion cannot see an <input>'s
    // rendered value, so this is checked on the computed style.
    const native = page.locator('input[aria-label="Jump to date"]')
    const style = await native.evaluate((el) => {
      const s = getComputedStyle(el)
      return { opacity: s.opacity, position: s.position, display: s.display, visibility: s.visibility }
    })
    expect(Number(style.opacity)).toBe(0)
    expect(style.position).toBe('absolute')
    // ...and still focusable, so keyboard users keep the real picker.
    expect(style.display).not.toBe('none')
    expect(style.visibility).not.toBe('hidden')
    await native.focus()
    await expect(native).toBeFocused()
  })

  // Fix 8 -- .book-summary-item-value is `white-space: nowrap` +
  // ellipsis, so "Dr Amogh Purohit · 9:00 AM" truncates to "Dr Amogh
  // Purohit ..." and the booked time -- the one thing being confirmed
  // -- is unreadable.
  test('summary panel wraps instead of truncating the doctor and time', async ({ page, request }) => {
    const scenario = await seedScenario(request)
    await page.setViewportSize(VIEWPORTS[0])
    await openBookAppointment(page, scenario, { pickSlot: true })

    const values = page.locator('.book-summary-item-value')
    const count = await values.count()
    expect(count).toBeGreaterThan(0)
    for (let i = 0; i < count; i++) {
      const v = await values.nth(i).evaluate((el) => {
        const s = getComputedStyle(el)
        return {
          overflowX: el.scrollWidth - el.clientWidth,
          whiteSpace: s.whiteSpace,
          textOverflow: s.textOverflow,
        }
      })
      expect(v.whiteSpace).not.toBe('nowrap')
      expect(v.textOverflow).not.toBe('ellipsis')
      expect(v.overflowX).toBeLessThanOrEqual(1)
    }

    // The booked time is readable in full.
    const slotValue = values.last()
    await expect(slotValue).toContainText(expectedSlotLabels()[0])
    await expect(slotValue).toContainText(scenario.doctor.name)
  })
})
