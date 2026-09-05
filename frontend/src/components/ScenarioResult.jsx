function Field({ label, value, full }) {
  return (
    <div className={full ? 'sm:col-span-2' : undefined}>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800">{value}</dd>
    </div>
  )
}

export default function ScenarioResult({ result, error }) {
  if (error) {
    return (
      <div role="alert" className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800">
        <p className="font-semibold">Something went wrong</p>
        <p className="mt-1">{error}</p>
      </div>
    )
  }

  if (!result) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
        Run a scenario above to see the result here.
      </div>
    )
  }

  const isAllowed = result.decision === 'ALLOW'

  return (
    <div
      className={`rounded-lg border p-5 ${
        isAllowed ? 'border-emerald-300 bg-emerald-50' : 'border-red-300 bg-red-50'
      }`}
    >
      <p className={`text-lg font-bold ${isAllowed ? 'text-emerald-700' : 'text-red-700'}`}>
        {isAllowed ? 'ALLOW' : 'BLOCKED'}
      </p>
      <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Why?" value={result.reason} full />
        <Field label="Original Purpose" value={result.originalPurpose} />
        <Field label="Requested Purpose" value={result.requestedPurpose} />
        <Field label="Expiry" value={result.expiry} />
        <Field label="Asset" value={result.assetLabel} />
        <Field label="Remediation Status" value={result.remediationStatus ?? 'N/A'} />
        <Field label="Corrective Action" value={result.correctiveAction} full />
      </dl>
    </div>
  )
}
