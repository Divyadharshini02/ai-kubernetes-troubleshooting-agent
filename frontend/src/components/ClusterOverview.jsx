function ClusterOverview({ cluster, onRefresh }) {
  const { state, data, message } = cluster

  return (
    <section className="card" aria-live="polite">
      <h2>Cluster overview</h2>
      {state === 'loading' && <p>Reading the cluster…</p>}
      {state === 'error' && <p className="status-error">Cluster unavailable: {message}</p>}
      {state === 'ok' && (
        <>
          <p>
            <strong>{data.context}</strong>, Kubernetes {data.server_version}
          </p>
          {data.truncated && (
            <p className="status-warn">The cluster holds more resources than one request returned, so this list is partial.</p>
          )}
          <h3>Nodes ({data.nodes.length})</h3>
          <ul className="plain-list">
            {data.nodes.map((node) => (
              <li key={node.name}>
                {node.name}:{' '}
                {node.ready === true && <span className="status-ok">Ready</span>}
                {node.ready === false && <span className="status-error">Not ready</span>}
                {node.ready === null && <span className="status-warn">Unknown</span>}
              </li>
            ))}
          </ul>
          <details>
            <summary>Pods ({data.pods.length})</summary>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Namespace</th>
                    <th>Pod</th>
                    <th>Phase</th>
                    <th>Ready</th>
                    <th>Restarts</th>
                  </tr>
                </thead>
                <tbody>
                  {data.pods.map((pod) => (
                    <tr key={`${pod.namespace}/${pod.name}`}>
                      <td>{pod.namespace}</td>
                      <td>{pod.name}</td>
                      <td>{pod.phase ?? 'Unknown'}</td>
                      <td>
                        {pod.ready_containers}/{pod.total_containers}
                      </td>
                      <td>{pod.restarts}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <p className="muted">Read at {new Date(data.collected_at).toLocaleString()}</p>
        </>
      )}
      <button type="button" onClick={onRefresh} disabled={state === 'loading'}>
        Refresh
      </button>
    </section>
  )
}

export default ClusterOverview
