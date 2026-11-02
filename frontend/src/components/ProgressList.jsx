const STAGE_LABELS = {
  created: 'Investigation created',
  connectivity_checked: 'Kubernetes connectivity checked',
  evidence_collected: 'Evidence collected',
  rules_evaluated: 'Diagnostic rules evaluated',
  ai_requested: 'AI explanation requested',
  ai_finished: 'AI explanation finished',
  report_saved: 'Report saved',
  completed: 'Investigation completed',
  failed: 'Investigation failed',
}

function ProgressList({ events, running, notice }) {
  return (
    <section className="card" aria-live="polite">
      <h2>Progress</h2>
      <ol className="progress">
        {events.map((event) => (
          <li key={event.sequence} className={event.stage === 'failed' ? 'status-error' : undefined}>
            <strong>{STAGE_LABELS[event.stage] ?? event.stage}</strong>
            <span className="muted"> {new Date(event.occurred_at).toLocaleTimeString()}</span>
            <br />
            {event.message}
          </li>
        ))}
      </ol>
      {running && <p>Waiting for the next step…</p>}
      {notice && <p className="status-warn">{notice}</p>}
    </section>
  )
}

export default ProgressList
