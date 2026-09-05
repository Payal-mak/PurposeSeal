const METRICS = [
  { key: 'activeGrants', label: 'Active Grants' },
  { key: 'trackedCopies', label: 'Tracked Copies' },
  { key: 'violations', label: 'Violations Detected', caption: 'this session' },
  { key: 'quarantinedAssets', label: 'Quarantined Assets' },
]

export default function SummaryMetrics({ metrics, loading }) {
  return (
    <section aria-label="Summary" className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {METRICS.map(({ key, label, caption }) => (
        <div key={key} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-sm text-slate-500">{label}</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{loading ? '—' : metrics[key]}</p>
          {caption ? <p className="text-xs text-slate-400">{caption}</p> : null}
        </div>
      ))}
    </section>
  )
}
