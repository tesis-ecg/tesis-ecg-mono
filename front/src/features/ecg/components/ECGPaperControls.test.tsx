// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ECGPaperControls } from './ECGPaperControls'

describe('ECGPaperControls', () => {
  it('no ofrece la acción de informe desde los controles de señal', () => {
    render(
      <ECGPaperControls
        paperSpeed={25}
        amplitude={20}
        onPaperSpeedChange={vi.fn()}
        onAmplitudeChange={vi.fn()}
        onScale
        onResetScale={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: 'Informe' })).toBeNull()
  })
})
