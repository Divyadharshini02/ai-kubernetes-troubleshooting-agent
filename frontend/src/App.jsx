import { useCallback, useEffect, useRef, useState } from 'react'
import AiAnalysis from './components/AiAnalysis.jsx'
import ChatPanel from './components/ChatPanel.jsx'
import ClusterOverview from './components/ClusterOverview.jsx'
import DiagnosisReport from './components/DiagnosisReport.jsx'
import EvidenceViewer from './components/EvidenceViewer.jsx'
import HistoryList from './components/HistoryList.jsx'
import InvestigationForm from './components/InvestigationForm.jsx'
import ProgressList from './components/ProgressList.jsx'
import {
  fetchClusterHealth,
  fetchHealth,
  fetchInvestigation,
  fetchInvestigations,
  followProgress,
  startInvestigation,
} from './services/api.js'
import './App.css'

// The stream can stop without saying why, so the saved status is also checked on a timer.
const STATUS_CHECK_MS = 5000
const MAX_STATUS_CHECKS = 120

function App() {
  const [health, setHealth] = useState({ state: 'loading' })
  const [cluster, setCluster] = useState({ state: 'loading' })
  const [history, setHistory] = useState({ state: 'loading', items: [] })
  const [startError, setStartError] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [followingId, setFollowingId] = useState(null)
  const [events, setEvents] = useState([])
  const [investigation, setInvestigation] = useState(null)
  const [notice, setNotice] = useState(null)
  // Only the most recent selection may update the page, whichever response arrives last.
  const latestSelection = useRef(null)

  const loadHealth = useCallback(() => {
    fetchHealth()
      .then((data) => setHealth({ state: 'ok', data }))
      .catch((error) => setHealth({ state: 'error', message: error.message }))
  }, [])

  const loadCluster = useCallback(() => {
    fetchClusterHealth()
      .then((data) => setCluster({ state: 'ok', data }))
      .catch((error) => setCluster({ state: 'error', message: error.message }))
  }, [])

  const loadHistory = useCallback(() => {
    fetchInvestigations()
      .then((items) => setHistory({ state: 'ok', items }))
      .catch((error) => setHistory({ state: 'error', items: [], message: error.message }))
  }, [])

  useEffect(() => {
    loadHealth()
    loadCluster()
    loadHistory()
  }, [loadHealth, loadCluster, loadHistory])

  useEffect(() => {
    if (!followingId) {
      return undefined
    }
    let stopped = false
    let checks = 0

    async function checkSavedStatus() {
      checks += 1
      try {
        const saved = await fetchInvestigation(followingId)
        if (stopped) {
          return
        }
        setNotice(null)
        if (saved.status !== 'running') {
          setEvents(saved.events)
          setInvestigation(saved)
          setFollowingId(null)
          loadHistory()
          return
        }
      } catch (error) {
        if (stopped) {
          return
        }
        setNotice(`The backend cannot be reached, so the status is unknown. Still trying. (${error.message})`)
      }
      if (checks >= MAX_STATUS_CHECKS) {
        setNotice('No final status was received. Check History later for the saved result.')
        setFollowingId(null)
        loadHistory()
      }
    }

    const stopFollowing = followProgress(followingId, {
      onProgress: (event) =>
        setEvents((current) =>
          current.some((item) => item.sequence === event.sequence) ? current : [...current, event],
        ),
      onClosed: checkSavedStatus,
    })
    const timer = setInterval(checkSavedStatus, STATUS_CHECK_MS)
    return () => {
      stopped = true
      clearInterval(timer)
      stopFollowing()
    }
  }, [followingId, loadHistory])

  function handleCheckAgain() {
    setHealth({ state: 'loading' })
    loadHealth()
  }

  function handleRefreshCluster() {
    setCluster({ state: 'loading' })
    loadCluster()
  }

  function handleRefreshHistory() {
    setHistory({ state: 'loading', items: [] })
    loadHistory()
  }

  async function handleInvestigate(namespace, explain) {
    setStartError(null)
    setNotice(null)
    try {
      const started = await startInvestigation(namespace, explain)
      latestSelection.current = started.id
      setSelectedId(started.id)
      setInvestigation(null)
      setEvents([])
      setFollowingId(started.id)
      loadHistory()
    } catch (error) {
      setStartError(error.message)
    }
  }

  async function handleSelect(id) {
    latestSelection.current = id
    setSelectedId(id)
    setFollowingId(null)
    setInvestigation(null)
    setEvents([])
    setNotice(null)
    try {
      const saved = await fetchInvestigation(id)
      if (latestSelection.current !== id) {
        return
      }
      setEvents(saved.events)
      if (saved.status === 'running') {
        setFollowingId(id)
      } else {
        setInvestigation(saved)
      }
    } catch (error) {
      if (latestSelection.current === id) {
        setNotice(`This investigation could not be read. (${error.message})`)
      }
    }
  }

  const pods = cluster.state === 'ok' ? cluster.data.pods : []
  const namespaces = [...new Set(pods.map((pod) => pod.namespace))].sort()

  return (
    <main className="page">
      <h1>AI Kubernetes Troubleshooting Agent</h1>

      <div className="layout">
        <div className="sidebar">
          <section className="card" aria-live="polite">
            <h2>Backend status</h2>
            {health.state === 'loading' && <p>Checking the backend…</p>}
            {health.state === 'ok' && (
              <p className="status-ok">
                {health.data.service} {health.data.version}: {health.data.status}
              </p>
            )}
            {health.state === 'error' && <p className="status-error">Backend unreachable: {health.message}</p>}
            <button type="button" onClick={handleCheckAgain} disabled={health.state === 'loading'}>
              Check again
            </button>
          </section>
          <ClusterOverview cluster={cluster} onRefresh={handleRefreshCluster} />
          <HistoryList history={history} selectedId={selectedId} onSelect={handleSelect} onRefresh={handleRefreshHistory} />
        </div>

        <div className="content">
          <InvestigationForm
            namespaces={namespaces}
            running={Boolean(followingId)}
            error={startError}
            onInvestigate={handleInvestigate}
          />
          {(events.length > 0 || followingId || notice) && (
            <ProgressList events={events} running={Boolean(followingId)} notice={notice} />
          )}
          {investigation && <DiagnosisReport investigation={investigation} />}
          {investigation?.report?.ai_analysis && <AiAnalysis analysis={investigation.report.ai_analysis} />}
          {investigation && investigation.status !== 'running' && (
            <ChatPanel key={`chat-${investigation.id}`} investigationId={investigation.id} />
          )}
          {investigation?.report?.evidence && (
            <EvidenceViewer key={investigation.id} evidence={investigation.report.evidence} />
          )}
        </div>
      </div>
    </main>
  )
}

export default App
