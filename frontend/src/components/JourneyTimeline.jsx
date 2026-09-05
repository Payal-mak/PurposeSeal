const EVENT_LABELS = {
  SOURCE_ASSET_CREATED: 'Asset Created',
  GRANT_CREATED: 'Grant Created',
  DATA_RETRIEVED: 'Data Retrieved',
  COPY_CREATED: 'Copy Created',
  DERIVED_ASSET_CREATED: 'Derived Copy Created',
  DATA_USE_ATTEMPTED: 'Use Attempted',
  USE_ALLOWED: 'Use Allowed',
  PURPOSE_VIOLATION: 'Violation Detected',
  USE_BLOCKED: 'Use Blocked',
  ASSET_QUARANTINED: 'Asset Quarantined',
  GRANT_REVOKED: 'Grant Revoked',
}

// PURPOSE_EXPIRED has no standalone audit event -- it is a reason_code
// carried inside a PURPOSE_VIOLATION event's details. This enriches the
// displayed label with that reason (e.g. "Purpose Expired") without any
// backend change: purely a presentation-layer read of existing data.
const VIOLATION_REASON_LABELS = {
  PURPOSE_EXPIRED: 'Purpose Expired',
  PURPOSE_MISMATCH: 'Purpose Mismatch',
  GRANT_REVOKED: 'Grant Revoked',
}

const NEGATIVE_EVENTS = new Set(['PURPOSE_VIOLATION', 'USE_BLOCKED', 'ASSET_QUARANTINED'])
const POSITIVE_EVENTS = new Set(['USE_ALLOWED'])

function describeEvent(event) {
  if (event.event_type === 'PURPOSE_VIOLATION') {
    const reasonLabel = VIOLATION_REASON_LABELS[event.details?.reason_code]
    if (reasonLabel) return `${reasonLabel} — Violation Detected`
  }
  return EVENT_LABELS[event.event_type] ?? event.event_type
}

export default function JourneyTimeline({ timeline }) {
  if (!timeline || timeline.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
        Run a scenario above to see its journey timeline here.
      </div>
    )
  }

  return (
    <ol aria-label="Journey timeline" className="space-y-2">
      {timeline.map((event, index) => {
        const isNegative = NEGATIVE_EVENTS.has(event.event_type)
        const isPositive = POSITIVE_EVENTS.has(event.event_type)
        const dot = isNegative ? 'bg-red-500' : isPositive ? 'bg-emerald-500' : 'bg-slate-400'

        return (
          <li
            key={`${event.entity_type}-${event.entity_id}-${event.event_type}-${index}`}
            className="flex items-start gap-3 rounded-md border border-slate-200 bg-white p-3"
          >
            <span className={`mt-1 h-2.5 w-2.5 flex-shrink-0 rounded-full ${dot}`} aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-slate-800">{describeEvent(event)}</p>
              <p className="text-xs text-slate-400">{new Date(event.created_at).toLocaleString()}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
