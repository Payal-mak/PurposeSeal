import { useCallback, useEffect, useState } from 'react'

import { getAsset, getGrant, getLineage, listAssets, listGrants, runScenario } from '../lib/api'
import JourneyTimeline from './JourneyTimeline'
import LineageGraph from './LineageGraph'
import ScenarioControls from './ScenarioControls'
import ScenarioResult from './ScenarioResult'
import SummaryMetrics from './SummaryMetrics'

const SCENARIO_TITLES = {
  legitimate: 'Valid Scenario',
  expired: 'Expired-Purpose Scenario',
  'purpose-mismatch': 'Purpose-Mismatch Scenario',
}

const DEFAULT_CORRECTIVE_ACTION =
  'No action needed — this use is fully within its authorized purpose and validity window.'

function findEventDetail(timeline, eventType, field) {
  const event = timeline.find((e) => e.event_type === eventType)
  return event?.details?.[field] ?? null
}

function formatTimestamp(isoString) {
  if (!isoString) return 'Unknown'
  return new Date(isoString).toLocaleString()
}

export default function Dashboard() {
  const [liveMetrics, setLiveMetrics] = useState({ activeGrants: 0, trackedCopies: 0, quarantinedAssets: 0 })
  const [metricsLoading, setMetricsLoading] = useState(true)
  const [metricsError, setMetricsError] = useState(null)
  const [violationsCount, setViolationsCount] = useState(0)

  const [runningKey, setRunningKey] = useState(null)
  const [result, setResult] = useState(null)
  const [scenarioError, setScenarioError] = useState(null)

  const [lineage, setLineage] = useState(null)
  const [grantStatusById, setGrantStatusById] = useState({})
  const [lineageError, setLineageError] = useState(null)

  const refreshLiveMetrics = useCallback(async () => {
    try {
      const [grants, allAssets, quarantinedAssets] = await Promise.all([
        listGrants(),
        listAssets(),
        listAssets({ state: 'QUARANTINED' }),
      ])
      setLiveMetrics({
        activeGrants: grants.filter((grant) => grant.status === 'ACTIVE').length,
        trackedCopies: allAssets.filter((asset) => asset.parent_asset_id !== null).length,
        quarantinedAssets: quarantinedAssets.length,
      })
      setMetricsError(null)
    } catch (err) {
      setMetricsError(err.message)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setMetricsLoading(true)
    refreshLiveMetrics().finally(() => {
      if (!cancelled) setMetricsLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [refreshLiveMetrics])

  const handleRun = async (scenarioKey) => {
    setRunningKey(scenarioKey)
    setScenarioError(null)

    try {
      const response = await runScenario(scenarioKey)
      const timeline = response.timeline ?? []

      let assetLabel = `Asset #${response.asset_id}`
      let originalPurpose = findEventDetail(timeline, 'GRANT_CREATED', 'purpose') ?? 'Unknown'
      let expiry = formatTimestamp(findEventDetail(timeline, 'GRANT_CREATED', 'expires_at'))
      let rootAssetId = response.asset_id

      try {
        const asset = await getAsset(response.asset_id)
        assetLabel = `${asset.name} (#${asset.id})`
        if (asset.origin_purpose) originalPurpose = asset.origin_purpose
        if (asset.origin_grant_expires_at) expiry = formatTimestamp(asset.origin_grant_expires_at)
        rootAssetId = asset.root_asset_id ?? asset.id
      } catch {
        // Asset detail is a nice-to-have for a friendlier label; the
        // timeline-derived values above already cover the essentials.
      }

      const requestedPurpose = findEventDetail(timeline, 'DATA_USE_ATTEMPTED', 'purpose') ?? 'Unknown'

      setResult({
        scenario: SCENARIO_TITLES[scenarioKey] ?? scenarioKey,
        decision: response.decision,
        reason: response.reason,
        originalPurpose,
        requestedPurpose,
        expiry,
        assetLabel,
        correctiveAction: response.remediation ?? DEFAULT_CORRECTIVE_ACTION,
        remediationStatus: response.remediation_status ?? null,
        timeline,
      })

      const newViolations = timeline.filter((event) => event.event_type === 'PURPOSE_VIOLATION').length
      if (newViolations > 0) {
        setViolationsCount((count) => count + newViolations)
      }

      await refreshLiveMetrics()
      await loadLineage(rootAssetId)
    } catch (err) {
      setScenarioError(err.message)
      setResult(null)
      setLineage(null)
    } finally {
      setRunningKey(null)
    }
  }

  const loadLineage = async (rootAssetId) => {
    try {
      const lineageData = await getLineage(rootAssetId)

      const uniqueGrantIds = [...new Set(lineageData.nodes.map((node) => node.origin_grant_id).filter(Boolean))]
      const grants = await Promise.all(uniqueGrantIds.map((grantId) => getGrant(grantId)))
      const statusById = Object.fromEntries(grants.map((grant) => [grant.id, grant.status]))

      setGrantStatusById(statusById)
      setLineage(lineageData)
      setLineageError(null)
    } catch (err) {
      setLineageError(err.message)
      setLineage(null)
    }
  }

  const metrics = { ...liveMetrics, violations: violationsCount }

  return (
    <div className="space-y-8">
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Summary</h2>
        {metricsError ? (
          <p role="alert" className="mb-2 text-sm text-red-600">
            Could not load live metrics: {metricsError}
          </p>
        ) : null}
        <SummaryMetrics metrics={metrics} loading={metricsLoading} />
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Scenario Controls</h2>
        <ScenarioControls onRun={handleRun} runningKey={runningKey} />
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Result</h2>
        <ScenarioResult result={result} error={scenarioError} />
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Journey Timeline</h2>
        <JourneyTimeline timeline={result?.timeline} />
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Data Lineage</h2>
        <LineageGraph lineage={lineage} grantStatusById={grantStatusById} error={lineageError} />
      </section>
    </div>
  )
}
