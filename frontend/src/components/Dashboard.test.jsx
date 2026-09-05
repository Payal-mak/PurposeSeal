import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Dashboard from './Dashboard'

afterEach(() => {
  vi.unstubAllGlobals()
})

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) })
}

const DEFAULT_ASSET_DETAIL = {
  id: 1,
  name: 'Demo Asset',
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
}

const DEFAULT_LINEAGE = {
  root_asset_id: 1,
  nodes: [DEFAULT_ASSET_DETAIL],
  edges: [],
}

function stubFetch({
  scenario,
  assets = [],
  quarantinedAssets = [],
  grants = [],
  assetDetail,
  lineage,
  grantDetail,
} = {}) {
  const fetchMock = vi.fn((url) => {
    const { pathname, search } = new URL(url)

    if (pathname === '/grants') return jsonResponse(grants)
    if (pathname === '/assets' && search.includes('QUARANTINED')) return jsonResponse(quarantinedAssets)
    if (pathname === '/assets') return jsonResponse(assets)
    if (/^\/assets\/\d+\/lineage$/.test(pathname)) return jsonResponse(lineage ?? DEFAULT_LINEAGE)
    if (pathname.startsWith('/assets/')) return jsonResponse(assetDetail ?? DEFAULT_ASSET_DETAIL)
    if (/^\/grants\/\d+$/.test(pathname)) return jsonResponse(grantDetail ?? { ...grants[0], status: 'ACTIVE' })
    if (pathname.startsWith('/demo/scenarios/') && scenario) return scenario(pathname)

    return jsonResponse({})
  })

  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const ALLOW_RESPONSE = {
  scenario: 'legitimate',
  decision: 'ALLOW',
  reason_code: 'WITHIN_PURPOSE_AND_VALIDITY',
  reason: "Use is within the grant's active window, matches its original purpose, and the operation is permitted.",
  asset_id: 3,
  grant_id: 1,
  remediation: null,
  remediation_status: null,
  timeline: [
    {
      event_type: 'SOURCE_ASSET_CREATED',
      entity_type: 'data_asset',
      entity_id: '1',
      details: { name: 'Patient Lab Result #104', asset_type: 'lab_result' },
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      event_type: 'GRANT_CREATED',
      entity_type: 'grant',
      entity_id: '1',
      details: { subject: 'researcher_01', purpose: 'clinical_trial_screening', expires_at: '2026-01-01T00:30:00Z' },
      created_at: '2026-01-01T00:00:01Z',
    },
    { event_type: 'DATA_RETRIEVED', entity_type: 'data_asset', entity_id: '2', details: {}, created_at: '2026-01-01T00:00:02Z' },
    { event_type: 'COPY_CREATED', entity_type: 'data_asset', entity_id: '3', details: {}, created_at: '2026-01-01T00:00:03Z' },
    {
      event_type: 'DATA_USE_ATTEMPTED',
      entity_type: 'data_asset',
      entity_id: '3',
      details: { purpose: 'clinical_trial_screening' },
      created_at: '2026-01-01T00:00:04Z',
    },
    { event_type: 'USE_ALLOWED', entity_type: 'data_asset', entity_id: '3', details: {}, created_at: '2026-01-01T00:00:05Z' },
  ],
}

const DENY_RESPONSE = {
  scenario: 'expired',
  decision: 'DENY',
  reason_code: 'PURPOSE_EXPIRED',
  reason: 'The purpose grant authorizing this data expired at 2026-01-01T00:10:00Z.',
  asset_id: 6,
  grant_id: 2,
  remediation: 'Issue a new purpose grant against the original asset if continued access is legitimate.',
  remediation_status: 'COMPLIANCE_REVIEW_REQUIRED',
  timeline: [
    {
      event_type: 'SOURCE_ASSET_CREATED',
      entity_type: 'data_asset',
      entity_id: '4',
      details: { name: 'Patient Lab Result #104', asset_type: 'lab_result' },
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      event_type: 'GRANT_CREATED',
      entity_type: 'grant',
      entity_id: '2',
      details: { purpose: 'clinical_trial_screening', expires_at: '2026-01-01T00:10:00Z' },
      created_at: '2026-01-01T00:00:01Z',
    },
    { event_type: 'DATA_RETRIEVED', entity_type: 'data_asset', entity_id: '5', details: {}, created_at: '2026-01-01T00:00:02Z' },
    { event_type: 'DERIVED_ASSET_CREATED', entity_type: 'data_asset', entity_id: '6', details: {}, created_at: '2026-01-01T00:00:03Z' },
    {
      event_type: 'DATA_USE_ATTEMPTED',
      entity_type: 'data_asset',
      entity_id: '6',
      details: { purpose: 'clinical_trial_screening' },
      created_at: '2026-01-01T00:11:00Z',
    },
    {
      event_type: 'PURPOSE_VIOLATION',
      entity_type: 'data_asset',
      entity_id: '6',
      details: { reason_code: 'PURPOSE_EXPIRED' },
      created_at: '2026-01-01T00:11:01Z',
    },
    { event_type: 'ASSET_QUARANTINED', entity_type: 'data_asset', entity_id: '6', details: {}, created_at: '2026-01-01T00:11:02Z' },
  ],
}

describe('Dashboard', () => {
  it('renders the dashboard sections', async () => {
    stubFetch()
    render(<Dashboard />)

    expect(screen.getByRole('region', { name: /summary/i })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /scenario controls/i })).toBeInTheDocument()
    expect(screen.getByText('Result')).toBeInTheDocument()
    expect(screen.getByText('Journey Timeline')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Active Grants')).toBeInTheDocument())
  })

  it('renders all three scenario control buttons', () => {
    stubFetch()
    render(<Dashboard />)

    expect(screen.getByRole('button', { name: /run valid scenario/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run expired-purpose scenario/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run purpose-mismatch scenario/i })).toBeInTheDocument()
  })

  it('shows a loading state and disables the buttons while a scenario runs', async () => {
    let resolveScenario
    const pending = new Promise((resolve) => {
      resolveScenario = resolve
    })
    stubFetch({ scenario: () => pending })
    render(<Dashboard />)

    const validButton = screen.getByRole('button', { name: /run valid scenario/i })
    fireEvent.click(validButton)

    expect(await screen.findByRole('status')).toHaveTextContent(/running/i)
    expect(validButton).toBeDisabled()
    expect(screen.getByRole('button', { name: /run expired-purpose scenario/i })).toBeDisabled()

    resolveScenario(jsonResponse(ALLOW_RESPONSE))
    await waitFor(() => expect(screen.getByText('ALLOW')).toBeInTheDocument())
  })

  it('displays a successful ALLOW result with its reason and timeline after running the valid scenario', async () => {
    stubFetch({ scenario: () => jsonResponse(ALLOW_RESPONSE) })
    render(<Dashboard />)

    fireEvent.click(screen.getByRole('button', { name: /run valid scenario/i }))

    expect(await screen.findByText('ALLOW')).toBeInTheDocument()
    expect(screen.getByText(/matches its original purpose/i)).toBeInTheDocument()
    expect(screen.getByText(/use allowed/i)).toBeInTheDocument()
    expect(screen.getByText(/no action needed/i)).toBeInTheDocument()
    expect(screen.getByText('N/A')).toBeInTheDocument() // no remediation status for an ALLOW decision

    // The button is re-enabled once the run finishes -- the scenario can be rerun.
    expect(screen.getByRole('button', { name: /run valid scenario/i })).not.toBeDisabled()
  })

  it('displays a BLOCKED violation result with corrective action, remediation status, and quarantine timeline steps', async () => {
    stubFetch({ scenario: () => jsonResponse(DENY_RESPONSE) })
    render(<Dashboard />)

    fireEvent.click(screen.getByRole('button', { name: /run expired-purpose scenario/i }))

    expect(await screen.findByText('BLOCKED')).toBeInTheDocument()
    expect(screen.getByText(/issue a new purpose grant/i)).toBeInTheDocument()
    expect(screen.getByText('COMPLIANCE_REVIEW_REQUIRED')).toBeInTheDocument()
    expect(screen.getByText(/purpose expired.*violation detected/i)).toBeInTheDocument()
    expect(screen.getByText(/asset quarantined/i)).toBeInTheDocument()
  })

  it('loads and displays the lineage graph for the evaluated asset after a scenario run', async () => {
    const lineage = {
      root_asset_id: 1,
      nodes: [
        { ...DEFAULT_ASSET_DETAIL, id: 1, name: 'Patient Lab Result #104' },
        { ...DEFAULT_ASSET_DETAIL, id: 3, name: 'Retrieved Copy', parent_asset_id: 1, root_asset_id: 1, origin_grant_id: 1 },
      ],
      edges: [{ parent_id: 1, child_id: 3 }],
    }
    stubFetch({ scenario: () => jsonResponse(ALLOW_RESPONSE), lineage, grantDetail: { id: 1, status: 'ACTIVE' } })
    render(<Dashboard />)

    fireEvent.click(screen.getByRole('button', { name: /run valid scenario/i }))

    expect(await screen.findByText('Patient Lab Result #104')).toBeInTheDocument()
    expect(screen.getByText('Retrieved Copy')).toBeInTheDocument()
  })

  it('shows a readable error message when the scenario request fails, without crashing, and allows a rerun', async () => {
    stubFetch({
      scenario: () => jsonResponse({ error_code: 'internal_error', message: 'An unexpected error occurred.' }, { ok: false, status: 500 }),
    })
    render(<Dashboard />)

    fireEvent.click(screen.getByRole('button', { name: /run valid scenario/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/unexpected error occurred/i)
    expect(screen.queryByText(/Error:/)).not.toBeInTheDocument()

    expect(screen.getByRole('button', { name: /run valid scenario/i })).not.toBeDisabled()
  })

  it('shows a readable error when the backend cannot be reached at all', async () => {
    stubFetch({ scenario: () => Promise.reject(new Error('network down')) })
    render(<Dashboard />)

    fireEvent.click(screen.getByRole('button', { name: /run valid scenario/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/could not reach the purposeseal backend/i)
  })
})
