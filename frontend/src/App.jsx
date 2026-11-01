import { useCallback, useEffect, useState } from 'react'
import { fetchHealth } from './services/api.js'
import './App.css'

function App() {
  const [health, setHealth] = useState({ state: 'loading' })

  const loadHealth = useCallback(() => {
    fetchHealth()
      .then((data) => setHealth({ state: 'ok', data }))
      .catch((error) => setHealth({ state: 'error', message: error.message }))
  }, [])

  useEffect(() => {
    loadHealth()
  }, [loadHealth])

  function handleCheckAgain() {
    setHealth({ state: 'loading' })
    loadHealth()
  }

  return (
    <main className="page">
      <h1>AI Kubernetes Troubleshooting Agent</h1>

      <section className="card" aria-live="polite">
        <h2>Backend status</h2>
        {health.state === 'loading' && <p>Checking the backend…</p>}
        {health.state === 'ok' && (
          <p className="status-ok">
            {health.data.service} {health.data.version}: {health.data.status}
          </p>
        )}
        {health.state === 'error' && (
          <p className="status-error">Backend unreachable: {health.message}</p>
        )}
        <button
          type="button"
          onClick={handleCheckAgain}
          disabled={health.state === 'loading'}
        >
          Check again
        </button>
      </section>
    </main>
  )
}

export default App
