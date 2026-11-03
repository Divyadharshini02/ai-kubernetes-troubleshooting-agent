const CONFIDENCE_TEXT = {
  high: 'High: several observations support this cause.',
  medium: 'Medium: the evidence supports it, but alternatives remain.',
  low: 'Low: the evidence is limited or ambiguous.',
  insufficient: 'Insufficient: the evidence does not support a conclusion.',
}

const ROOT_CAUSE_KIND = {
  confirmed: 'Shown directly by the cited evidence',
  hypothesis: 'Hypothesis: an inference that may be wrong',
}

function EvidenceIds({ ids }) {
  if (!ids.length) {
    return null
  }
  return (
    <p className="muted">
      Evidence:{' '}
      {ids.map((id) => (
        <code key={id} className="evidence-id">
          {id}
        </code>
      ))}
    </p>
  )
}

function TextList({ title, items }) {
  if (!items.length) {
    return null
  }
  return (
    <>
      <h4>{title}</h4>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </>
  )
}

/** Advice only. Commands are shown as text for a person to review: nothing here runs them. */
export function RemediationSteps({ steps }) {
  if (!steps.length) {
    return null
  }
  return (
    <>
      <h4>Suggested remediation (advice only: review before running anything yourself)</h4>
      <ol className="remediation">
        {steps.map((step) => (
          <li key={step.action}>
            <strong>{step.action}</strong>{' '}
            <span className={`badge risk-${step.risk}`}>Risk: {step.risk}</span>
            <p>{step.why}</p>
            {step.commands.length > 0 && <pre>{step.commands.join('\n')}</pre>}
            <EvidenceIds ids={step.evidence_ids} />
          </li>
        ))}
      </ol>
    </>
  )
}

function Unavailable({ analysis }) {
  return (
    <section className="card ai-panel" aria-labelledby="ai-analysis-title">
      <h2 id="ai-analysis-title">AI root cause analysis</h2>
      <p className="status-warn">
        <strong>Not available.</strong> {analysis.message}
      </p>
      <p className="muted">The deterministic diagnosis above is complete without it.</p>
    </section>
  )
}

/** The investigation-level analysis from the LLM, kept visibly apart from the deterministic diagnosis. */
function AiAnalysis({ analysis }) {
  if (!analysis) {
    return null
  }
  if (analysis.status === 'not_needed') {
    return (
      <section className="card ai-panel" aria-labelledby="ai-analysis-title">
        <h2 id="ai-analysis-title">AI root cause analysis</h2>
        <p className="muted">Not needed: the rules found no problems and the evidence was complete, so no request was made.</p>
      </section>
    )
  }
  if (analysis.status !== 'generated' || !analysis.content) {
    return <Unavailable analysis={analysis} />
  }
  const { content } = analysis
  return (
    <section className="card ai-panel" aria-labelledby="ai-analysis-title">
      <h2 id="ai-analysis-title">AI root cause analysis</h2>
      <p className="ai-label">
        {analysis.notice}
        {analysis.model && <span className="muted"> Model: {analysis.model}.</span>}
      </p>
      <p>{content.summary}</p>

      <h3>Root cause</h3>
      <p>{content.root_cause.statement}</p>
      <p>
        <span className={`badge kind-${content.root_cause.kind}`}>{ROOT_CAUSE_KIND[content.root_cause.kind]}</span>
      </p>
      <EvidenceIds ids={content.root_cause.evidence_ids} />

      <h4>Confidence</h4>
      <p>
        <span className={`badge strength-${content.confidence}`}>{content.confidence}</span>{' '}
        {CONFIDENCE_TEXT[content.confidence]}
      </p>
      {analysis.confidence_limited && (
        <p className="status-warn">
          Lowered from {analysis.model_confidence} by the application, because the evidence does not support more.
        </p>
      )}

      <TextList title="Reasoning" items={content.reasoning} />

      <h4>Supporting evidence</h4>
      <ul>
        {content.supporting_evidence.map((item) => (
          <li key={item.evidence_id + item.observation}>
            {item.observation} <code className="evidence-id">{item.evidence_id}</code>
          </li>
        ))}
      </ul>

      {content.alternative_causes.length > 0 && (
        <>
          <h4>Alternative causes (these may be wrong)</h4>
          <ul>
            {content.alternative_causes.map((item) => (
              <li key={item.statement}>
                {item.statement} <EvidenceIds ids={item.evidence_ids} />
              </li>
            ))}
          </ul>
        </>
      )}

      <TextList title="Limitations" items={content.limitations} />
      <RemediationSteps steps={content.remediation} />
      <TextList title="How to verify" items={content.verification_steps} />
      {analysis.evidence_omitted > 0 && (
        <p className="muted">{analysis.evidence_omitted} evidence records were not sent to the LLM because of size limits.</p>
      )}
    </section>
  )
}

export default AiAnalysis
