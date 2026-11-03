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

/** An error from the backend, keeping its HTTP status and code so callers can explain it. */
export class ApiError extends Error {
  constructor(message, status, code) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function errorCode(response) {
  try {
    return (await response.clone().json())?.detail?.code
  } catch {
    return undefined
  }
}

async function request(path, options) {
  const response = await fetch(path, options)
  if (!response.ok) {
    const code = await errorCode(response)
    throw new ApiError(await errorMessage(response), response.status, code)
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

// The same limit as the backend, which also enforces it.
export const MAX_CHAT_MESSAGE_CHARS = 2000

export function fetchChat(id) {
  return request(`${INVESTIGATIONS}/${encodeURIComponent(id)}/chat`)
}

/** Ask about a saved investigation. The backend uses saved evidence, so nothing is collected again. */
export function sendChatMessage(id, message) {
  return request(`${INVESTIGATIONS}/${encodeURIComponent(id)}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })
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
