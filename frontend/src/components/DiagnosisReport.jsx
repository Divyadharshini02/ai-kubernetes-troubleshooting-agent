const OUTCOME_LABELS = {
  problems_found: 'Problems found',
  no_problems_found: 'No problems found',
  insufficient_evidence: 'Insufficient evidence',
}

const STATUS_LABELS = {
  problem_detected: 'Problem detected',
  insufficient_evidence: 'Insufficient evidence',
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

function Recommendation({ recommendation }) {
  return (
    <details>
      <summary>{recommendation.action}</summary>
      <p>{recommendation.reason}</p>
      <p>
        <strong>Risk:</strong> {recommendation.risk}
      </p>
      <p>
        <strong>Expected impact:</strong> {recommendation.expected_impact}
      </p>
      <h5>How to apply</h5>
      <pre>{recommendation.how_to_apply.join('\n')}</pre>
      <h5>How to verify</h5>
      <pre>{recommendation.how_to_verify.join('\n')}</pre>
    </details>
  )
}

function AiExplanation({ explanation, notice }) {
  if (!explanation) {
    return null
  }
  if (explanation.status !== 'generated') {
    return <p className="muted">AI explanation unavailable: {explanation.message}</p>
  }
  const { content } = explanation
  return (
    <div className="ai-box">
      <h4>AI explanation</h4>
      <p className="muted">{notice}</p>
      <p>{content.explanation}</p>
      <p>
        <strong>Impact:</strong> {content.impact}
      </p>
      <TextList title="Evidence summary" items={content.evidence_summary} />
      <TextList title="Investigation steps" items={content.investigation_steps} />
      <TextList title="Limitations" items={content.limitations} />
    </div>
  )
}

function Diagnosis({ diagnosis, llm, startOpen }) {
  const resource = diagnosis.affected_resource
  const explanation = llm?.explanations.find((item) => item.diagnosis_id === diagnosis.diagnosis_id)
  return (
    <details className="diagnosis" open={startOpen}>
      <summary>
        <strong>{diagnosis.title}</strong>
        <div className="muted">
          {resource.kind} {resource.namespace}/{resource.name}
          {resource.container && `, container ${resource.container}`}
        </div>
        <span className={`badge ${diagnosis.status}`}>{STATUS_LABELS[diagnosis.status]}</span>{' '}
        <span className={`badge strength-${diagnosis.evidence_strength}`}>Evidence strength: {diagnosis.evidence_strength}</span>
      </summary>
      <p>{diagnosis.explanation}</p>
      <h4>Confirmed facts</h4>
      <ul>
        {diagnosis.confirmed_facts.map((fact) => (
          <li key={fact.statement}>
            {fact.statement}
            {fact.excerpt && <pre>{fact.excerpt}</pre>}
          </li>
        ))}
      </ul>
      <TextList title="Possible causes (these may be wrong)" items={diagnosis.possible_causes.map((cause) => cause.statement)} />
      <TextList title="Missing evidence" items={diagnosis.missing_evidence} />
      <TextList title="Conflicting evidence" items={diagnosis.conflicting_evidence} />
      {diagnosis.recommendations.length > 0 && (
        <>
          <h4>Recommended fixes (manual: the application never applies them)</h4>
          {diagnosis.recommendations.map((recommendation) => (
            <Recommendation key={recommendation.action} recommendation={recommendation} />
          ))}
        </>
      )}
      <AiExplanation explanation={explanation} notice={llm?.notice} />
    </details>
  )
}

function aiStatusText(investigation) {
  const llm = investigation.report?.llm
  if (!investigation.explain_requested || !llm) {
    return 'Not requested'
  }
  return llm.message ? `${llm.status}: ${llm.message}` : llm.status
}

function DiagnosisReport({ investigation }) {
  const { report, error } = investigation

  return (
    <section className="card">
      <h2>Diagnosis report: {investigation.namespace}</h2>
      {investigation.status === 'running' && <p>This investigation is still running.</p>}
      {investigation.status === 'failed' && (
        <p className="status-error">
          The investigation failed, so there is no diagnosis. {error?.message} ({error?.code})
        </p>
      )}
      {report && (
        <>
          <p className={report.outcome === 'no_problems_found' ? 'status-ok' : 'status-warn'}>
            <strong>{OUTCOME_LABELS[report.outcome]}.</strong> {report.summary}
          </p>
          {report.evidence_completeness === 'partial' && (
            <p className="status-warn">Some evidence could not be collected, so this result may be incomplete.</p>
          )}
          <dl className="facts">
            <dt>Context</dt>
            <dd>{report.context}</dd>
            <dt>Examined</dt>
            <dd>
              {report.resources_examined.pods} pods, {report.resources_examined.containers} containers,{' '}
              {report.resources_examined.services} services
            </dd>
            <dt>Evidence collected</dt>
            <dd>{new Date(report.evidence_collected_at).toLocaleString()}</dd>
            <dt>Duration</dt>
            <dd>{investigation.duration_seconds?.toFixed(1)} seconds</dd>
            <dt>AI explanation</dt>
            <dd>{aiStatusText(investigation)}</dd>
            <dt>Recovery verification</dt>
            <dd>Not verified</dd>
          </dl>
          {report.diagnoses.map((diagnosis, index) => (
            <Diagnosis key={diagnosis.diagnosis_id} diagnosis={diagnosis} llm={report.llm} startOpen={index === 0} />
          ))}
        </>
      )}
    </section>
  )
}

export default DiagnosisReport
