import { useState } from 'react'

function InvestigationForm({ namespaces, running, error, onInvestigate }) {
  const [namespace, setNamespace] = useState('')
  const [explain, setExplain] = useState(false)

  function handleSubmit(event) {
    event.preventDefault()
    onInvestigate(namespace.trim(), explain)
  }

  return (
    <section className="card">
      <h2>Investigate</h2>
      <form onSubmit={handleSubmit}>
        <label htmlFor="namespace">Namespace</label>
        <input
          id="namespace"
          list="known-namespaces"
          value={namespace}
          onChange={(event) => setNamespace(event.target.value)}
          placeholder="sample-app"
          required
          autoComplete="off"
          spellCheck="false"
        />
        <datalist id="known-namespaces">
          {namespaces.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        <label className="checkbox">
          <input type="checkbox" checked={explain} onChange={(event) => setExplain(event.target.checked)} />
          Include AI explanation
        </label>
        <p className="muted">
          If the LLM is enabled on the backend, this sends names, messages and log excerpts to the provider. The
          diagnosis does not depend on it.
        </p>
        <button type="submit" disabled={running}>
          {running ? 'Investigating…' : 'Investigate Cluster'}
        </button>
      </form>
      {error && <p className="status-error">{error}</p>}
    </section>
  )
}

export default InvestigationForm
