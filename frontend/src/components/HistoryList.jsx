function resultText(item) {
  if (item.status === 'running') {
    return 'Running'
  }
  if (item.status === 'failed') {
    return `Failed: ${item.error?.code ?? 'unknown'}`
  }
  return `${item.outcome?.replaceAll('_', ' ')}, ${item.diagnosis_count} diagnoses`
}

function HistoryList({ history, selectedId, onSelect, onRefresh }) {
  const { state, items, message } = history

  return (
    <section className="card">
      <h2>History</h2>
      {state === 'loading' && <p>Reading saved investigations…</p>}
      {state === 'error' && <p className="status-error">History unavailable: {message}</p>}
      {state === 'ok' && items.length === 0 && <p>No investigations have been saved yet.</p>}
      {state === 'ok' && items.length > 0 && (
        <ul className="plain-list history">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={`history-item ${item.id === selectedId ? 'selected' : ''}`}
                aria-current={item.id === selectedId}
                onClick={() => onSelect(item.id)}
              >
                <strong>{item.namespace}</strong>
                <span className={item.status === 'failed' ? 'status-error' : undefined}>{resultText(item)}</span>
                <span className="muted">{new Date(item.started_at).toLocaleString()}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" onClick={onRefresh} disabled={state === 'loading'}>
        Refresh
      </button>
    </section>
  )
}

export default HistoryList
