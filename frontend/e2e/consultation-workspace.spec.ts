import { test, expect } from '@playwright/test'
import { addAllergy, bookAndCheckIn, loginAdmin, payAppointment, seedBasicScenario } from './helpers'

// Drives the redesigned ConsultationWorkspace against a real backend +
// database (frontend/e2e/README.md explains the one-time setup). Each
// test seeds its own department/doctor/patient (seedBasicScenario)
// rather than sharing fixtures, so tests can run in any order without
// interfering with each other's state.

// There is no URL route for a single consultation -- AdminApp.tsx
// keeps navigation as plain React state (goToConsultation sets
// consultationAppointmentId then goTo('consultation')), reached in the
// real app by clicking a queue/appointment row or a global-search
// result. GlobalSearchBar's own onOpenAppointment handler is
// AdminApp's goToSearchResult, which opens ConsultationWorkspace
// directly for a CHECKED_IN appointment (AdminApp.tsx:241-247) -- the
// same, real path a receptionist would use, and the only one that
// doesn't depend on AppointmentsPanel's own redesign already being
// wired correctly.
async function loginAndOpenConsultation(page: import('@playwright/test').Page, patientName: string) {
  await page.goto('/admin')
  await page.locator('#username').fill('e2e-admin')
  await page.locator('input[type="password"]').fill('e2e-password-123')
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/auth/staff/login')),
    page.locator('button[type="submit"]').click(),
  ])
  await page.waitForLoadState('networkidle')

  await page.getByLabel('Global search').fill(patientName)
  // The dropdown groups results under "Patients" and "Appointments"
  // headings (GlobalSearchBar.tsx) -- both can match the same name
  // substring, but only the Appointments-group result opens
  // ConsultationWorkspace (the Patients-group one opens a read-only
  // timeline modal instead), so the group must be scoped explicitly
  // rather than picking the first name match.
  const appointmentsGroup = page
    .locator('.global-search-group')
    .filter({ has: page.locator('.global-search-group-label', { hasText: 'Appointments' }) })
  // .first(): a patient can have more than one matching appointment
  // result (e.g. two separate checked-in encounters in the same test)
  // -- getPatientTimeline is patient-scoped regardless of which one
  // opens ConsultationWorkspace, so any match is an equally valid way
  // in for tests that only care about patient-level data.
  const result = appointmentsGroup.locator('.global-search-result', { hasText: patientName }).first()
  await result.waitFor({ state: 'visible', timeout: 10000 })
  await result.click()
  await page.waitForLoadState('networkidle')
}

test.describe('ConsultationWorkspace', () => {
  test('patient banner shows name, age/sex, UHID, token, allergy count, elapsed time', async ({ page, request }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    await addAllergy(request, token, scenario.patient.id, 'Penicillin', { severity: 'SEVERE', reaction: 'Anaphylaxis' })
    await addAllergy(request, token, scenario.patient.id, 'Sulfa drugs', { severity: 'MILD', reaction: 'Rash' })
    const appointmentId = await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })
    await payAppointment(request, token, appointmentId)

    await loginAndOpenConsultation(page, scenario.patient.name)

    const banner = page.locator('.consult-banner')
    await expect(banner).toContainText(scenario.patient.name)
    await expect(banner).toContainText('UHID')
    await expect(banner).toContainText(scenario.patient.uhid)
    await expect(banner).toContainText(/Token #?\d+/)
    await expect(banner).toContainText('2 active allergies')
    await expect(banner).toContainText(/Started/)
  })

  test('vitals trend is sourced from the patient timeline, not latest-only', async ({ page, request }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)

    // Two separate encounters (two separate checked-in visits) for the
    // same patient, each with its own vitals -- the whole point of the
    // trend requirement is that it spans more than the current
    // encounter's own latest reading, which getLatestVitals alone
    // (appointment-scoped) can never show. The timeline orders visits
    // by the encounter's own started_at (real check-in time), not the
    // appointment's scheduled start_at, so simply checking in twice in
    // sequence -- both a few minutes in the future, same as
    // bookAndCheckIn's own default -- already produces two encounters
    // in the right order without needing (confirm rejects) a
    // backdated start_at.
    const firstAppointmentId = await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
      startAt: new Date(Date.now() + 5 * 60_000).toISOString(),
    })
    await request.post(`http://127.0.0.1:8000/api/appointments/${firstAppointmentId}/vitals`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { bp_systolic: 132, bp_diastolic: 84, pulse: 78 },
    })

    // A distinct, later slot for the doctor -- the default 20-minute
    // appointment type means +5min's slot alone would otherwise
    // collide with this one (create_appointment_service rejects
    // overlapping slots for the same doctor).
    const secondAppointmentId = await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
      startAt: new Date(Date.now() + 40 * 60_000).toISOString(),
    })
    await request.post(`http://127.0.0.1:8000/api/appointments/${secondAppointmentId}/vitals`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { bp_systolic: 148, bp_diastolic: 92, pulse: 86 },
    })

    let timelineCalled = false
    page.on('request', (req) => {
      if (req.url().includes(`/patients/${scenario.patient.id}/timeline`)) timelineCalled = true
    })

    await loginAndOpenConsultation(page, scenario.patient.name)
    await expect(page.locator('.consult-vitals-card')).toContainText('148/92')

    expect(timelineCalled).toBe(true)
    // The trend line must reflect the change from the earlier visit,
    // not just restate the latest reading with nothing to compare to.
    await expect(page.locator('.consult-vitals-card')).toContainText(/132\/84/)
  })

  test('allergy list shows severity for each entry', async ({ page, request }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    await addAllergy(request, token, scenario.patient.id, 'Penicillin', { severity: 'SEVERE', reaction: 'Anaphylaxis' })
    await addAllergy(request, token, scenario.patient.id, 'Sulfa drugs', { severity: 'MILD', reaction: 'Rash' })
    await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })

    await loginAndOpenConsultation(page, scenario.patient.name)

    const allergyCard = page.locator('.consult-allergy-card')
    await expect(allergyCard).toContainText('Penicillin')
    await expect(allergyCard).toContainText('SEVERE')
    await expect(allergyCard).toContainText('Sulfa drugs')
    await expect(allergyCard).toContainText('MILD')
  })

  test('orders tab shows lab lifecycle status only -- no Verify/Release actions, with a worklist link', async ({
    page,
    request,
  }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    const appointmentId = await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })
    const orderRes = await request.post(`http://127.0.0.1:8000/api/appointments/${appointmentId}/orders`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { order_type: 'LAB', description: 'Lipid profile', priority: 'ROUTINE' },
    })
    const order = await orderRes.json()
    await request.post(`http://127.0.0.1:8000/api/appointments/${appointmentId}/orders/${order.id}/collect-sample`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { sample_type: 'Blood' },
    })

    await loginAndOpenConsultation(page, scenario.patient.name)
    await page.locator('.consult-tab-orders').click()

    const orderCard = page.locator('.consult-order-card', { hasText: 'Lipid profile' })
    await expect(orderCard).toContainText('Collected')
    await expect(orderCard.getByRole('button', { name: /verify/i })).toHaveCount(0)
    await expect(orderCard.getByRole('button', { name: /release/i })).toHaveCount(0)
    await expect(orderCard.getByRole('button', { name: /worklist/i })).toBeVisible()
  })

  test('an allergy conflict on a prescription item is labelled as a name-only match, not a class check', async ({
    page,
    request,
  }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    await addAllergy(request, token, scenario.patient.id, 'Penicillin', { severity: 'SEVERE', reaction: 'Anaphylaxis' })
    await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })

    await loginAndOpenConsultation(page, scenario.patient.name)
    await page.locator('.consult-tab-prescription').click()
    await page.getByRole('button', { name: '+ Add medication' }).click()
    // "Penicillin" must literally appear in the drug name for the
    // real substring-match backend logic (allergy_check_service.py)
    // to fire -- a clinically-similar but differently-named drug
    // (e.g. Co-amoxiclav) would NOT trigger it, which is exactly the
    // point of labelling this a name-only match.
    await page.getByLabel(/medicine name/i).fill('Penicillin V 500mg')
    await page.getByLabel(/dosage/i).fill('1 tab')
    await page.getByLabel(/frequency/i).fill('twice daily')
    await page.getByLabel(/duration/i).fill('5 days')
    await page.getByRole('button', { name: /save|add/i }).last().click()

    const warning = page.locator('.consult-allergy-conflict-warning')
    await expect(warning).toContainText('Name match only')
    await expect(warning).toContainText('not a drug-class check', { ignoreCase: true })
    await expect(warning).not.toContainText(/drug class|ingredient|clinical(ly)? equivalent/i)
  })

  test('disposition options never include Admit to IPD', async ({ page, request }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })

    await loginAndOpenConsultation(page, scenario.patient.name)

    const dispositionGroup = page.locator('[role="radiogroup"][aria-label="Disposition"]')
    await expect(dispositionGroup).toBeVisible()
    await expect(dispositionGroup).not.toContainText(/admit to ipd/i)
  })

  test('consultation locks immediately on Complete, with no time-based grace window, then Amend re-enables it', async ({
    page,
    request,
  }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })

    await loginAndOpenConsultation(page, scenario.patient.name)

    await page.getByLabel(/chief complaint/i).fill('Headache for 4 days')
    await page.getByLabel(/diagnosis/i).fill('Tension headache')
    await page.getByRole('button', { name: /complete/i }).click()
    await expect(page.getByRole('button', { name: /complete/i })).toHaveCount(0)

    // Locked immediately -- no 15-minute countdown, no editable window
    // of any kind (this app has no server-side time gate to honor, so
    // the UI must never pretend one exists).
    await expect(page.getByLabel(/chief complaint/i)).toBeDisabled()
    await expect(page.getByText(/minutes? (left|remaining)/i)).toHaveCount(0)

    const amendButton = page.getByRole('button', { name: /amend/i })
    await expect(amendButton).toBeVisible()
    await amendButton.click()
    await expect(page.getByLabel(/chief complaint/i)).toBeEnabled()
  })

  // There is no update endpoint for a prescription item -- app/api/
  // pharmacy.py exposes only POST .../prescription/items and DELETE
  // .../prescription/items/{item_id}. Any "Edit" affordance therefore
  // has to compose a delete and an add, and every ordering of those two
  // calls is unsafe: delete-first loses the line outright if the re-add
  // never happens, add-first leaves a duplicate medication order if the
  // delete fails. Neither is acceptable for a prescription, so the
  // button is gone until a real PATCH endpoint exists (tracked in
  // issue #131) and Remove + Add is the honest workflow in the
  // meantime. This spec pins that: no Edit control, and remove/re-add
  // still works.
  test('prescription items have no Edit control; remove and re-add is the pre-send workflow, and after send only whole-prescription cancel remains', async ({
    page,
    request,
  }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })

    await loginAndOpenConsultation(page, scenario.patient.name)
    await page.locator('.consult-tab-prescription').click()
    await page.getByRole('button', { name: '+ Add medication' }).click()
    await page.getByLabel(/medicine name/i).fill('Amlodipine 10mg')
    await page.getByLabel(/dosage/i).fill('1 tab')
    await page.getByLabel(/frequency/i).fill('once daily')
    await page.getByLabel(/duration/i).fill('30 days')
    await page.getByRole('button', { name: /save|add/i }).last().click()

    const item = page.locator('.consult-prescription-item', { hasText: 'Amlodipine' })
    await expect(item).toBeVisible()
    // No Edit control at all -- not merely hidden after send.
    await expect(item.getByRole('button', { name: /edit/i })).toHaveCount(0)

    // Remove takes the line away for real (the backend DELETE), and the
    // clinician re-adds a corrected one. Asserted end to end rather
    // than on the button alone, because the whole point of dropping
    // Edit is that this path is the only one that touches the server.
    await item.getByRole('button', { name: /remove/i }).click()
    await expect(page.locator('.consult-prescription-item', { hasText: 'Amlodipine' })).toHaveCount(0)

    await page.getByRole('button', { name: '+ Add medication' }).click()
    await page.getByLabel(/medicine name/i).fill('Amlodipine 5mg')
    await page.getByLabel(/dosage/i).fill('1 tab')
    await page.getByLabel(/frequency/i).fill('once daily')
    await page.getByLabel(/duration/i).fill('30 days')
    await page.getByRole('button', { name: /save|add/i }).last().click()

    const corrected = page.locator('.consult-prescription-item', { hasText: 'Amlodipine 5mg' })
    await expect(corrected).toBeVisible()
    await expect(page.getByText(/items can be removed and re-added until sent/i)).toBeVisible()

    await page.getByRole('button', { name: /send to pharmacy/i }).click()

    await expect(corrected.getByRole('button', { name: /remove/i })).toHaveCount(0)
    await expect(page.getByText(/only the whole prescription can be cancelled/i)).toBeVisible()
    await expect(page.getByRole('button', { name: /cancel prescription/i })).toBeVisible()
  })

  // The two amber treatments must not be confusable: a recorded SEVERE
  // allergy is a verified clinical fact, while the prescription
  // conflict card is a name-only substring match (allergy_check_
  // service.py) that explicitly is not a clinical check. Option B --
  // amber stays exclusive to the recorded fact, the name match goes
  // neutral.
  test('a SEVERE allergy and the name-match warning are visually distinguishable', async ({ page, request }) => {
    const token = await loginAdmin(request)
    const scenario = await seedBasicScenario(request, token)
    await addAllergy(request, token, scenario.patient.id, 'Amoxicillin', {
      reaction: 'anaphylaxis',
      severity: 'SEVERE',
    })
    await bookAndCheckIn(request, token, {
      doctorId: scenario.doctor.id,
      patientId: scenario.patient.id,
      appointmentTypeId: scenario.appointmentType.id,
    })

    await loginAndOpenConsultation(page, scenario.patient.name)

    const severePill = page.locator('.pill.severity-severe').first()
    await expect(severePill).toBeVisible()
    const severeBg = await severePill.evaluate((el) => getComputedStyle(el).backgroundColor)

    await page.locator('.consult-tab-prescription').click()
    await page.getByRole('button', { name: '+ Add medication' }).click()
    await page.getByLabel(/medicine name/i).fill('Amoxicillin 500mg')
    await page.getByLabel(/dosage/i).fill('1 cap')
    await page.getByRole('button', { name: /save|add/i }).last().click()

    const conflict = page.locator('.consult-allergy-conflict-warning')
    await expect(conflict).toBeVisible()
    const conflictBg = await conflict.evaluate((el) => getComputedStyle(el).backgroundColor)

    // The actual requirement: the two backgrounds are not the same
    // colour. Asserted on computed style rather than a class name so a
    // future restyle that collapses them back together fails here.
    expect(conflictBg).not.toBe(severeBg)
    // And the name match specifically is not wearing the clinical
    // warning tint -- amber is reserved for the recorded allergy.
    expect(conflictBg).not.toBe('rgb(253, 242, 223)')

    // The CHECK pill inside that card is pinned the same way. Without
    // this, a restyle could re-amber the pill alone and silently undo
    // the distinction: the card would still pass the assertions above
    // while the badge the eye actually lands on went back to matching
    // .pill.severity-severe.
    const checkPill = conflict.locator('.pill.severity-check')
    await expect(checkPill).toBeVisible()
    const checkPillBg = await checkPill.evaluate((el) => getComputedStyle(el).backgroundColor)
    expect(checkPillBg).not.toBe(severeBg)
    expect(checkPillBg).not.toBe('rgb(253, 242, 223)')
  })
})
