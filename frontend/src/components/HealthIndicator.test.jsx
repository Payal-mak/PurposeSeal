import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import HealthIndicator from './HealthIndicator'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HealthIndicator', () => {
  it('shows a checking state before the health check resolves', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    )

    render(<HealthIndicator />)

    expect(screen.getByText(/checking backend/i)).toBeInTheDocument()
  })

  it('shows connected once the backend responds successfully', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'ok' }) })),
    )

    render(<HealthIndicator />)

    expect(await screen.findByText(/backend connected/i)).toBeInTheDocument()
  })

  it('shows unavailable when the backend request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('network error'))),
    )

    render(<HealthIndicator />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
  })

  it('shows unavailable when the backend responds with a non-OK status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ ok: false, status: 500 })),
    )

    render(<HealthIndicator />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
  })
})
