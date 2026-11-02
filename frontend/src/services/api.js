const INVESTIGATIONS = '/api/investigations'

export async function fetchHealth() {
  const response = await fetch('/api/health')
  if (!response.ok) {
    throw new Error(`Backend responded with HTTP ${response.status}`)
  }
  return response.json()
}

async function errorMessage(response) {
  const fallback = `Backend responded with HTTP ${response.status}`
  try {
    const { detail } = await response.json()
    if (detail?.message) {
      return detail.message
    }
    if (Array.isArray(detail) && detail[0]?.msg) {
      return `The backend rejected this request: ${detail[0].msg}`
    }
    return fallback
  } catch {
    return fallback
  }
}

async function request(path, options) {
  const response = await fetch(path, options)
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export function fetchClusterHealth() {
  return request('/api/cluster/health')
}

export function startInvestigation(namespace, explain) {
  return request(INVESTIGATIONS, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ namespace, explain }),
  })
}

export function fetchInvestigations() {
  return request(INVESTIGATIONS)
}

export function fetchInvestigation(id) {
  return request(`${INVESTIGATIONS}/${encodeURIComponent(id)}`)
}

/**
 * Follow an investigation's Server-Sent Events. The browser reconnects by itself and
 * resumes from the last event. Returns a function that stops following.
 */
export function followProgress(id, { onProgress, onClosed }) {
  const source = new EventSource(`${INVESTIGATIONS}/${encodeURIComponent(id)}/events`)
  const finish = () => {
    source.close()
    onClosed()
  }
  source.addEventListener('progress', (event) => onProgress(JSON.parse(event.data)))
  source.addEventListener('end', finish)
  source.addEventListener('stream_limit', finish)
  source.onerror = () => {
    // CONNECTING means the browser is retrying. CLOSED means it has given up.
    if (source.readyState === EventSource.CLOSED) {
      finish()
    }
  }
  return () => source.close()
}
