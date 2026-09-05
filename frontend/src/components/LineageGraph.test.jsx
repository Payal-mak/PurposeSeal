import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import LineageGraph from './LineageGraph'

const SAMPLE_LINEAGE = {
  root_asset_id: 1,
  nodes: [
    {
      id: 1,
      name: 'Patient Lab Result #104',
      asset_type: 'lab_result',
      parent_asset_id: null,
      root_asset_id: null,
      effective_root_asset_id: 1,
      origin_grant_id: null,
      origin_purpose: null,
      origin_grant_expires_at: null,
      state: 'ACTIVE',
      fingerprint: null,
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 2,
      name: 'Retrieved Copy',
      asset_type: 'lab_result',
      parent_asset_id: 1,
      root_asset_id: 1,
      effective_root_asset_id: 1,
      origin_grant_id: 1,
      origin_purpose: 'clinical_trial_screening',
      origin_grant_expires_at: '2026-01-01T00:30:00Z',
      state: 'ACTIVE',
      fingerprint: 'abc',
      created_at: '2026-01-01T00:00:01Z',
    },
    {
      id: 3,
      name: 'Analysis Dataset',
      asset_type: 'dataset',
      parent_asset_id: 2,
      root_asset_id: 1,
      effective_root_asset_id: 1,
      origin_grant_id: 1,
      origin_purpose: 'clinical_trial_screening',
      origin_grant_expires_at: '2026-01-01T00:30:00Z',
      state: 'QUARANTINED',
      fingerprint: 'def',
      created_at: '2026-01-01T00:00:02Z',
    },
  ],
  edges: [
    { parent_id: 1, child_id: 2 },
    { parent_id: 2, child_id: 3 },
  ],
}

describe('LineageGraph', () => {
  it('renders every node returned by the lineage API', () => {
    render(<LineageGraph lineage={SAMPLE_LINEAGE} grantStatusById={{ 1: 'ACTIVE' }} />)

    expect(screen.getByText('Patient Lab Result #104')).toBeInTheDocument()
    expect(screen.getByText('Retrieved Copy')).toBeInTheDocument()
    expect(screen.getByText('Analysis Dataset')).toBeInTheDocument()
  })

  it('renders an edge for every parent/child relationship', async () => {
    render(<LineageGraph lineage={SAMPLE_LINEAGE} grantStatusById={{ 1: 'ACTIVE' }} />)

    // @xyflow/react measures each node (ResizeObserver -> a requestAnimationFrame
    // -> a state update) before it draws edges between them; waitFor lets that
    // settle instead of asserting against the DOM's initial, unmeasured paint.
    await waitFor(() => {
      expect(document.querySelectorAll('.react-flow__edge')).toHaveLength(SAMPLE_LINEAGE.edges.length)
    })
  })

  it('makes the quarantined state visible on the affected node', () => {
    render(<LineageGraph lineage={SAMPLE_LINEAGE} grantStatusById={{ 1: 'ACTIVE' }} />)

    expect(screen.getByText('QUARANTINED')).toBeInTheDocument()
  })

  it('shows a purpose-expired status when the shared origin grant has expired', () => {
    render(<LineageGraph lineage={SAMPLE_LINEAGE} grantStatusById={{ 1: 'EXPIRED' }} />)

    expect(screen.getAllByText('PURPOSE EXPIRED').length).toBeGreaterThan(0)
  })

  it('handles empty/missing lineage without crashing', () => {
    render(<LineageGraph lineage={null} grantStatusById={{}} />)

    expect(screen.getByText(/run a scenario above/i)).toBeInTheDocument()
  })

  it('handles an API error gracefully', () => {
    render(<LineageGraph lineage={null} grantStatusById={{}} error="Could not reach the PurposeSeal backend. Is it running?" />)

    expect(screen.getByRole('alert')).toHaveTextContent(/could not reach the purposeseal backend/i)
  })

  it('shows node details -- asset, parent, root, grant, purpose, expiry, status -- when a node is clicked', () => {
    render(<LineageGraph lineage={SAMPLE_LINEAGE} grantStatusById={{ 1: 'ACTIVE' }} />)

    fireEvent.click(screen.getByText('Retrieved Copy'))

    expect(screen.getByText('Retrieved Copy (#2)')).toBeInTheDocument()
    // Parent, root, and associated grant are all "#1" for this fixture.
    expect(screen.getAllByText('#1', { exact: true })).toHaveLength(3)
    expect(screen.getByText('clinical_trial_screening')).toBeInTheDocument()
  })
})
