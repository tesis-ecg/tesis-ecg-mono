import { describe, expect, it } from 'vitest'

import { safeInternalPath } from './safeRedirect'

describe('safeInternalPath', () => {
  it.each([
    [null, '/'],
    ['', '/'],
    ['https://evil.example', '/'],
    ['//evil.example', '/'],
    ['/\\evil.example', '/'],
    ['/patients\n', '/'],
    ['/patients/123?tab=studies', '/patients/123?tab=studies'],
  ])('normaliza %j a %j', (input, expected) => {
    expect(safeInternalPath(input)).toBe(expected)
  })
})
