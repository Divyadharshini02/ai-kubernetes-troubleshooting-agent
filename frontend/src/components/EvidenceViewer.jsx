import { useState } from 'react'

const ALL = 'all'

function EvidenceViewer({ evidence }) {
  const [category, setCategory] = useState(ALL)
  const categories = [...new Set(evidence.records.map((record) => record.category))].sort()
  const records = evidence.records.filter((record) => category === ALL || record.category === category)

  return (
    <section className="card">
      <h2>Evidence ({evidence.records.length} records)</h2>
      <p className="muted">
        Completeness: {evidence.completeness}.{' '}
        {Object.entries(evidence.status_counts)
          .map(([status, count]) => `${status}: ${count}`)
          .join(', ')}
        . Text from the cluster is shown as data, and is redacted when collected.
      </p>
      <label htmlFor="evidence-category">Show</label>
      <select id="evidence-category" value={category} onChange={(event) => setCategory(event.target.value)}>
        <option value={ALL}>All categories</option>
        {categories.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
      <ul className="plain-list evidence">
        {records.map((record) => (
          <li key={record.evidence_id}>
            <details>
              <summary>
                {record.resource_kind} {record.resource_name ?? ''}{' '}
                <span className={record.error ? 'status-error' : 'muted'}>
                  {record.status}, {record.temporality}
                </span>
              </summary>
              <p className="muted">
                {record.evidence_id}, from {record.source}
                {record.truncated && ', truncated'}
                {record.redactions > 0 && `, ${record.redactions} redactions`}
              </p>
              {record.error && (
                <p className="status-error">
                  {record.error.message} ({record.error.code})
                </p>
              )}
              <pre>{JSON.stringify(record.data, null, 2)}</pre>
            </details>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default EvidenceViewer
