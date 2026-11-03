import { useCallback, useEffect, useState } from 'react'
import { fetchChat, MAX_CHAT_MESSAGE_CHARS, sendChatMessage } from '../services/api.js'
import { RemediationSteps } from './AiAnalysis.jsx'

const KIND_LABELS = {
  answer: 'AI answer',
  off_topic: 'Topic guard: no AI request was made',
  clarify: 'Topic guard: no AI request was made',
  policy: 'Topic guard: no AI request was made',
  unavailable: 'AI unavailable: deterministic result',
}

const SCOPE_LABELS = {
  investigation: 'About this investigation',
  general: "General guidance, not based on this cluster's evidence",
}

// Explains each failure without losing the typed message. Nothing is ever resent automatically.
function sendErrorText(error) {
  switch (error.status) {
    case 404:
      return 'This investigation no longer exists, so the message was not sent.'
    case 409:
    case 429:
    case 503:
      return `${error.message} Your message was kept, so you can send it again.`
    case 422:
      return `The message was not accepted: ${error.message}`
    default:
      return `The message could not be sent: ${error.message}. Your message was kept, so you can send it again.`
  }
}

function time(value) {
  return value ? new Date(value).toLocaleTimeString() : ''
}

function AssistantReply({ message, onFollowUp }) {
  const { answer } = message
  return (
    <>
      <p className={`muted kind-${message.kind}`}>{KIND_LABELS[message.kind] ?? message.kind}</p>
      <p>{message.text}</p>
      {answer && (
        <>
          <p className="muted">{SCOPE_LABELS[answer.scope]}</p>
          {answer.evidence_ids.length > 0 && (
            <p className="muted">
              Evidence:{' '}
              {answer.evidence_ids.map((id) => (
                <code key={id} className="evidence-id">
                  {id}
                </code>
              ))}
            </p>
          )}
          <RemediationSteps steps={answer.remediation} />
          {answer.limitations.length > 0 && (
            <ul className="muted">
              {answer.limitations.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
          {answer.follow_up_questions.length > 0 && (
            <div className="follow-ups">
              {answer.follow_up_questions.map((question) => (
                <button key={question} type="button" className="link-button" onClick={() => onFollowUp(question)}>
                  {question}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </>
  )
}

/** Chat about one finished investigation. Replies use its saved evidence: the cluster is not contacted again. */
function ChatPanel({ investigationId }) {
  const [history, setHistory] = useState({ state: 'loading', messages: [] })
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [sendError, setSendError] = useState(null)

  const loadHistory = useCallback(() => {
    fetchChat(investigationId)
      .then((messages) => setHistory({ state: 'ok', messages }))
      .catch((error) => setHistory({ state: 'error', messages: [], message: error.message }))
  }, [investigationId])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  function handleRetry() {
    setHistory({ state: 'loading', messages: [] })
    loadHistory()
  }

  async function send() {
    const message = draft.trim()
    if (!message || pending) {
      return
    }
    setPending(true)
    setSendError(null)
    try {
      const exchange = await sendChatMessage(investigationId, message)
      setHistory((current) => ({ state: 'ok', messages: [...current.messages, exchange.question, exchange.reply] }))
      setDraft('')
    } catch (error) {
      setSendError(sendErrorText(error))
    } finally {
      setPending(false)
    }
  }

  function handleSubmit(event) {
    event.preventDefault()
    send()
  }

  function handleKeyDown(event) {
    // Enter sends. Shift+Enter starts a new line.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send()
    }
  }

  return (
    <section className="card chat" aria-labelledby="chat-title">
      <h2 id="chat-title">Ask about this investigation</h2>
      <p className="muted">
        Answers use this investigation's saved evidence and may contain mistakes. Commands are advice only: the
        application never runs them. To check a fix, run a new investigation.
      </p>

      {history.state === 'loading' && history.messages.length === 0 && <p>Loading the conversation…</p>}
      {history.state === 'error' && (
        <p className="status-error">
          The conversation could not be loaded: {history.message}{' '}
          <button type="button" onClick={handleRetry}>
            Retry
          </button>
        </p>
      )}
      {history.state === 'ok' && history.messages.length === 0 && (
        <p className="muted">No messages yet. Ask why something failed, what the evidence shows, or how to fix it.</p>
      )}

      {history.messages.length > 0 && (
        <ol className="plain-list chat-messages" aria-live="polite">
          {history.messages.map((message) => (
            <li key={message.sequence} className={`chat-message ${message.role}`}>
              <div className="muted">
                {message.role === 'user' ? 'You' : 'Assistant'} <time dateTime={message.created_at}>{time(message.created_at)}</time>
              </div>
              {message.role === 'user' ? <p>{message.text}</p> : <AssistantReply message={message} onFollowUp={setDraft} />}
            </li>
          ))}
        </ol>
      )}

      <form onSubmit={handleSubmit}>
        <label htmlFor="chat-message">Your question</label>
        <textarea
          id="chat-message"
          value={draft}
          maxLength={MAX_CHAT_MESSAGE_CHARS}
          rows={3}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={pending}
          placeholder="Why is the pod failing?"
        />
        <div className="chat-actions">
          <span className="muted" aria-live="polite">
            {draft.length} / {MAX_CHAT_MESSAGE_CHARS}
          </span>
          <button type="submit" disabled={pending || !draft.trim()}>
            {pending ? 'Waiting for a reply…' : 'Send'}
          </button>
        </div>
      </form>
      {pending && (
        <p className="muted" role="status">
          Thinking… a free model can take a minute or more.
        </p>
      )}
      {sendError && (
        <p className="status-error" role="alert">
          {sendError}
        </p>
      )}
    </section>
  )
}

export default ChatPanel
