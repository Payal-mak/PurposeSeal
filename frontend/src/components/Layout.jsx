import HealthIndicator from './HealthIndicator'

const NAV_ITEMS = ['Grants', 'Retrieved Data', 'Audit Trail']

export default function Layout({ children }) {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">PurposeSeal</h1>
            <p className="text-sm text-slate-500">Purpose-bound access lifecycle enforcement</p>
          </div>
          <HealthIndicator />
        </div>
        <nav className="mx-auto max-w-5xl px-6">
          <ul className="flex gap-6 text-sm text-slate-400">
            {NAV_ITEMS.map((item) => (
              <li key={item} className="cursor-not-allowed py-2" title="Coming soon">
                {item}
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
    </div>
  )
}
