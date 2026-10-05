import { expect, test } from '@playwright/test'
import { watchForFailures } from '../support/failures'

// The sidebar is the app's table of contents. Walking it catches the failures
// an agent most often causes by accident: a broken route, a page that throws on
// mount, or an API the page calls that now answers 500.
const PAGES = [
  { link: 'Inicio', path: '/', heading: /^(Buen día|Buenas tardes|Buenas noches)/ },
  { link: 'Pacientes', path: '/patients', heading: 'Pacientes' },
  { link: 'Dispositivos', path: '/devices', heading: 'Dispositivos' },
  { link: 'Estudios', path: '/studies', heading: 'Estudios' },
  { link: 'Alertas', path: '/alerts', heading: 'Alertas' },
] as const

// Only admins see these two, so they are walked only when the test user is one.
const ADMIN_PAGES = [
  { link: 'Usuarios', path: '/users', heading: 'Usuarios' },
  { link: 'Simulador de chalecos', path: '/__sim/vest', heading: 'Simulador de chalecos' },
] as const

test('every sidebar page renders without errors', async ({ page }) => {
  const failures = watchForFailures(page)
  await page.goto('/')

  const nav = page.getByRole('navigation', { name: 'Navegación principal' })
  const isAdmin = (await nav.getByRole('link', { name: 'Usuarios' }).count()) > 0
  const pages = isAdmin ? [...PAGES, ...ADMIN_PAGES] : PAGES

  for (const { link, path, heading } of pages) {
    await nav.getByRole('link', { name: link }).click()
    await expect.poll(() => new URL(page.url()).pathname).toBe(path)
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    // Let the page's own data requests settle so a 500 is seen before the next page.
    await page.waitForLoadState('networkidle')
  }

  expect(failures).toEqual([])
})

test('an unknown route shows the not-found page, not a blank screen', async ({ page }) => {
  await page.goto('/this-page-does-not-exist')
  await expect(page).not.toHaveURL(/\/login/)
  await expect(page.getByRole('heading', { level: 1, name: 'Página no encontrada' })).toBeVisible()
})
