import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import App from './App'

vi.stubGlobal(
  'fetch',
  vi.fn(() => new Promise(() => {})),
)

describe('App', () => {
  it('renders the PurposeSeal product name', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: /purposeseal/i })).toBeInTheDocument()
  })

  it('renders the navigation items', () => {
    render(<App />)
    expect(screen.getByText('Grants')).toBeInTheDocument()
    expect(screen.getByText('Audit Trail')).toBeInTheDocument()
  })
})
