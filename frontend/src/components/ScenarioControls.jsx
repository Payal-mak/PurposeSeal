const SCENARIOS = [
  { key: 'legitimate', label: 'Run Valid Scenario' },
  { key: 'expired', label: 'Run Expired-Purpose Scenario' },
  { key: 'purpose-mismatch', label: 'Run Purpose-Mismatch Scenario' },
]

export default function ScenarioControls({ onRun, runningKey }) {
  const disabled = runningKey !== null

  return (
    <section aria-label="Scenario controls" className="space-y-2">
      <div className="flex flex-wrap gap-3">
        {SCENARIOS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => onRun(key)}
            disabled={disabled}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition-colors disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {runningKey === key ? 'Running…' : label}
          </button>
        ))}
      </div>
      {runningKey ? (
        <p role="status" className="text-sm text-slate-500">
          Running scenario… this only takes a moment.
        </p>
      ) : null}
    </section>
  )
}
