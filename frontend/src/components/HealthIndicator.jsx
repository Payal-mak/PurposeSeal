import { useEffect, useState } from 'react'

import { fetchHealth } from '../lib/api'

const STATUS_STYLES = {
  checking: { dot: 'bg-amber-400', text: 'text-amber-700', label: 'Checking backend...' },
  online: { dot: 'bg-emerald-500', text: 'text-emerald-700', label: 'Backend connected' },
  offline: { dot: 'bg-red-500', text: 'text-red-700', label: 'Backend unavailable' },
}

export default function HealthIndicator() {
  const [status, setStatus] = useState('checking')

  useEffect(() => {
    let cancelled = false

    fetchHealth()
      .then(() => {
        if (!cancelled) setStatus('online')
      })
      .catch(() => {
        if (!cancelled) setStatus('offline')
      })

    return () => {
      cancelled = true
    }
  }, [])

  const { dot, text, label } = STATUS_STYLES[status]

  return (
    <div className="flex items-center gap-2 text-sm" role="status">
      <span className={`h-2.5 w-2.5 rounded-full ${dot}`} aria-hidden="true" />
      <span className={text}>{label}</span>
    </div>
  )
}
