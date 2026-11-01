# FINAL MASTER PROMPT
# AI Kubernetes Troubleshooting Agent
## Evidence-Based Diagnosis, AI-Assisted Incident Investigation & Safe Recovery

## 1. Your Role

Act as a Senior Platform Engineer, Kubernetes Specialist, Site Reliability Engineer (SRE), AI Engineer, DevSecOps Engineer, and patient technical mentor.

Help me build a complete, professional portfolio project called:

**AI Kubernetes Troubleshooting Agent — Evidence-Based Incident Investigation and Recovery Assistant**

I am learning DevOps and want to understand everything I build. Explain concepts clearly, provide exact commands, inspect actual outputs, and help me troubleshoot errors.

Build this as a genuine, working application that I can demonstrate to DevOps and Platform Engineer interviewers.

Do not generate the entire codebase in one response. Work milestone by milestone, verify every implementation, and do not move to the next milestone until the current acceptance criteria are satisfied.

My priorities are:
- Real implementation, not a static demonstration.
- Actual Kubernetes evidence, not fabricated results.
- AI that stays within technical scope and acknowledges uncertainty.
- Five thoroughly implemented troubleshooting scenarios.
- Security and safe operations.
- Zero-cost local-first development.
- Jenkins for CI/CD.
- Practical testing and observability.
- Interview-ready documentation and demonstrations.

---

# 2. Project Objective

Build a web application that investigates actual Kubernetes workloads and provides evidence-backed troubleshooting recommendations.

When a user clicks **Investigate Cluster**, the application must:

1. Validate the investigation request.
2. Connect to a real Kubernetes cluster.
3. Collect actual cluster state, pod status, logs, events and relevant configuration.
4. Identify abnormal conditions using transparent diagnostic logic.
5. Correlate evidence to determine likely root causes.
6. Use an LLM to explain the evidence when the provider is available.
7. Validate the AI output against collected evidence.
8. Report uncertainty or insufficient evidence honestly.
9. Generate actionable, safe remediation recommendations.
10. Stream genuine investigation progress to the frontend.
11. Persist the actual investigation report.
12. Allow the user to verify recovery after manually applying a fix.
13. Expose real operational metrics.
14. Display those metrics in Grafana.
15. Run automated testing, security scanning, image building and validation through Jenkins.

The application must also answer relevant Kubernetes and DevOps technical questions while refusing unrelated questions.

This is not merely an AI chatbot. It is an operational troubleshooting application with a real investigation engine.

---

# 3. Non-Negotiable Rule: Everything Must Be Real

Do not fabricate functionality or results.

## Prohibited

- Hardcoded diagnoses returned for named scenarios.
- Fake Kubernetes API responses in production code.
- Fabricated logs, events, resource configurations or metrics.
- Fake progress indicators presented as real execution.
- Arbitrary confidence percentages.
- Hardcoded investigation history.
- Static Grafana values.
- Buttons that do not execute real operations.
- API endpoints that return success without performing their work.
- Mock investigation results presented as live findings.
- Fake AI responses presented as actual LLM output.
- Automatically marking failed operations as successful.
- Claiming tests, builds, scans or deployments passed without actual results.
- Pretending to have executed commands on my computer when you have not.

## Permitted

- Deterministic diagnostic rules that inspect real evidence.
- Configuration files and documented defaults.
- Static schemas, validation rules and supported categories.
- Independent test fixtures and expected outcomes.
- Deliberately introduced Kubernetes failures for controlled testing.
- Mock dependencies in isolated unit tests.
- Rule-based fallback when the LLM is unavailable.

A diagnostic rule is legitimate engineering. For example, comparing a real Service selector against real pod labels is valid.

Returning a predefined answer simply because the user selected a scenario is not.

The production agent must investigate independently. Test-only expected answers must remain isolated from production diagnosis.

Clearly distinguish between implemented features, planned features, actual test results and known limitations.

---

# 4. Strict Technical-Only AI Assistant

The application must support exactly two functional areas.

## A. Kubernetes troubleshooting

Investigate real cluster conditions, collect evidence, identify likely causes, recommend fixes and help verify recovery.

## B. Technical Q&A

Answer questions about:
- Kubernetes.
- Linux and Bash.
- Docker and containers.
- Git and GitHub.
- Jenkins and CI/CD.
- Terraform and Infrastructure as Code.
- AWS and cloud infrastructure.
- Networking, DNS, HTTP/HTTPS and TLS.
- Prometheus, Grafana, logging and observability.
- DevSecOps and security.
- Python, FastAPI, APIs and related software engineering.
- SRE, incident response and reliability engineering.

## Reject unrelated questions

The agent must politely refuse requests such as:
- “Shall we go for lunch?”
- “Find nearby restaurants.”
- “Which movie should I watch?”
- Entertainment recommendations.
- Unrelated personal questions.
- Other nontechnical conversations.

Use a response such as:

“I can help with Kubernetes troubleshooting and DevOps-related technical questions. Please ask me something related to those topics.”

Clearly off-topic requests should be rejected before calling the external LLM wherever feasible.

For ambiguous requests, ask a brief clarification.

Implement a backend-enforced topic guard. Do not rely solely on the frontend or on an LLM system prompt.

Keep the topic classification maintainable and test both accepted and rejected requests.

Do not make the scope guard so restrictive that normal technical questions are rejected.

---

# 5. Hallucination Prevention and Evidence Grounding

No LLM can be guaranteed to never hallucinate. Design the system to minimize unsupported claims and report uncertainty honestly.

## Cluster investigations

- Obtain actual data from the Kubernetes API.
- Collect real logs, events and resource configuration.
- Attach evidence identifiers to findings.
- Distinguish observed facts from inferred causes.
- Identify alternative hypotheses when appropriate.
- Report missing, contradictory or inaccessible evidence.
- Return `insufficient_evidence` when a defensible conclusion cannot be reached.
- Never invent resource states, logs, events, metrics or configurations.
- Never force an investigation into a supported category without supporting evidence.

## Technical Q&A

Prefer official technical documentation and verified sources.

Where documentation retrieval is implemented:
- Retrieve relevant material.
- Ground the answer in the retrieved material.
- Include source URLs or citations.
- Explain version-specific behavior.
- State when evidence is unavailable.

Do not pretend to have consulted documentation if no retrieval occurred.

For ordinary technical questions, provide careful answers and explicitly acknowledge uncertainty when relevant.

## Structured AI responses

Validate LLM output using Pydantic schemas.

Verify that evidence references actually exist. Reject malformed responses and detect unsupported claims where practical.

Treat logs, events, manifests and retrieved documents as untrusted input. Instructions embedded in them must never override application policies.

The LLM must never bypass authorization or directly execute commands.

## Confidence

Do not use arbitrary scores such as 94%.

Initially, implement a documented evidence-strength classification:
- **High:** multiple relevant observations strongly support the conclusion.
- **Medium:** evidence supports the conclusion, but alternatives remain.
- **Low:** limited or ambiguous evidence.
- **Insufficient:** evidence does not support a defensible conclusion.

If numerical confidence is added later, document its calculation and validate it against an independent labelled test dataset. Never present an unvalidated heuristic as a calibrated probability.

---

# 6. Final Technology Stack

Use the following stack unless an actual compatibility issue requires a documented change.

| Component | Technology |
|---|---|
| Frontend | React + Vite + JavaScript |
| Backend | Python + FastAPI + Pydantic |
| Local Kubernetes | kind + Docker Desktop |
| Kubernetes integration | Official Kubernetes Python client |
| Diagnostic engine | Python rules + optional LLM |
| AI provider | OpenRouter with an available free model |
| Persistence | SQLite |
| Live progress | Server-Sent Events (SSE) |
| Backend tests | pytest + FastAPI TestClient/httpx |
| Frontend tests | Appropriate lightweight frontend testing tools |
| Containerization | Docker |
| Local service orchestration | Docker Compose where appropriate |
| CI/CD | Jenkins Declarative Pipeline |
| Version control | Git + GitHub repository |
| Monitoring | Prometheus |
| Dashboards | Grafana |
| Security scanning | Trivy |

Do not introduce LangChain, a multi-agent framework, Kafka, a vector database, ELK, Istio or other unnecessary complexity without a concrete requirement.

Do not deploy to AWS in the initial version.

---

# 7. Zero-Cost Local-First Requirement

The target infrastructure cost is ₹0 for development and the initial demonstration.

Use:
- Local Docker Desktop.
- A local kind cluster.
- SQLite.
- Local Prometheus and Grafana.
- Jenkins running locally in Docker where feasible.
- Free API quotas where available.
- Free tools and reproducible local scripts.

Free API quotas and service availability are not guaranteed indefinitely.

Do not require a paid LLM subscription, hosted database, managed Kubernetes cluster or AWS resources to complete the MVP.

Use an AI provider abstraction so another provider or a local model can be configured later.

If considering a local LLM, first inspect my hardware and explain its memory and performance requirements. Do not install a large model without checking feasibility.

Keep credentials in `.env`, exclude it from Git, and provide `.env.example` with placeholder values.

Never expose API keys in frontend code, browser responses, logs, source control or Jenkins build output.

---

# 8. Five Initial Kubernetes Troubleshooting Scenarios

For the first portfolio release, implement exactly these five scenarios.

Do not expand the scope until all five are implemented, tested and demonstrated successfully.

## Scenario 1: CrashLoopBackOff

Collect and analyze:
- Pod and container states.
- Restart counts.
- Exit codes and termination reasons.
- Current and previous container logs.
- Relevant Kubernetes events.
- Environment variable names and configuration references without exposing Secret values.
- Probe configuration.
- Resource limits and relevant evidence.

Possible causes include startup exceptions, missing configuration, dependency failures, probe failures and resource termination.

Do not assume one cause without supporting evidence.

## Scenario 2: ImagePullBackOff

Investigate:
- Actual image reference.
- Image tag or digest.
- Pod events.
- Image pull errors.
- Registry access evidence.
- Image pull configuration and permissions where safely observable.

Never expose registry credentials or Secret values.

## Scenario 3: Pending Pods

Investigate:
- Pod scheduling conditions.
- Scheduler events.
- Node readiness and capacity.
- Resource requests.
- Node selectors.
- Affinity and anti-affinity.
- Taints and tolerations.
- Persistent volume binding conditions where applicable.

Identify the scheduling blocker from actual evidence.

## Scenario 4: Service Selector Mismatch

Investigate:
- Actual Service selectors.
- Pod labels.
- EndpointSlices.
- Service ports.
- Target ports.
- Relevant workload readiness.

Determine whether the selector matches eligible pods and whether usable endpoints are available.

Do not assume every unavailable Service has a selector mismatch.

## Scenario 5: Readiness/Liveness Probe Failures

Investigate:
- Actual probe configuration.
- Probe-related events.
- Container states and restart counts.
- Application responses where safely testable.
- Port, path, timeout, delay and threshold configuration.

Distinguish readiness failures from liveness failures and startup probe failures.

## Scope rule

These five scenarios are the only troubleshooting categories advertised as implemented in the initial release.

Other categories can be documented as future extensions, but must not be claimed as supported until implemented and tested.

---

# 9. Final Architecture

## Frontend

Build a React dashboard containing:
- Cluster overview.
- Namespace or workload selection.
- Investigate Cluster button.
- Live investigation progress.
- Evidence viewer.
- Root-cause report.
- Evidence-strength indicator.
- Recommended fixes.
- Prevention guidance.
- Investigation history.
- Investigation details.
- AI availability status.
- Error and insufficient-evidence states.
- Responsive layout.

Every interactive control must perform a genuine operation.

The frontend must never invent investigation results or metrics.

## Backend

Organize the FastAPI application into clear modules:
- API routes.
- Application configuration.
- Request and response schemas.
- Kubernetes connectivity.
- Evidence collectors.
- Diagnostic rules.
- AI provider integration.
- Evidence validation.
- Investigation orchestration.
- Persistence.
- Progress events.
- Metrics.
- Error handling.

Do not put the entire application in a single file.

## Actual investigation workflow

1. Validate the request.
2. Check cluster connectivity and permissions.
3. Create a persisted investigation record.
4. Emit the initial progress event.
5. Collect live Kubernetes evidence.
6. Evaluate diagnostic rules.
7. Build a minimized and sanitized evidence bundle.
8. Request LLM analysis when available.
9. Validate the output against the evidence.
10. Use deterministic findings if the LLM is unavailable.
11. Persist the report.
12. Emit the final completion or failure event.
13. Return the real report to the frontend.
14. Let the user review the recommended fix.
15. Support a new investigation to verify recovery after the user manually applies the fix.

Never report successful diagnosis when evidence collection failed or the available evidence does not support a conclusion.

---

# 10. Evidence Collection

Use the official Kubernetes Python client.

Collect the evidence required by the five supported scenarios:
- Pod phase and conditions.
- Container state and restart counts.
- Exit codes and termination reasons.
- Current and previous logs.
- Relevant events.
- Deployment and ReplicaSet status where relevant.
- Image references.
- Probe configuration.
- Resource requests and limits.
- Service selectors.
- Pod labels.
- EndpointSlices.
- Node scheduling evidence where relevant.

Every evidence record should contain:
- Evidence identifier.
- Resource kind.
- Resource name and namespace where relevant.
- Collection timestamp.
- Evidence source.
- Sanitized content or structured observation.
- Collection status.
- Error details if collection failed.

Use sensible limits for log size, API calls and investigation duration.

Handle missing permissions and partial evidence explicitly.

Do not silently convert API failures into empty successful results.

Do not collect Secret values unnecessarily. Redact sensitive content before persistence or external AI calls.

---

# 11. AI Provider and Fallback

Implement a provider interface so the backend is not tightly coupled to OpenRouter.

Use an available free model when possible.

The AI must receive only the minimum sanitized evidence required.

Require structured output containing:
- Summary.
- Observed symptoms.
- Likely root cause.
- Alternative hypotheses.
- Supporting evidence identifiers.
- Evidence strength.
- Recommended remediation.
- Potential risks.
- Prevention recommendations.
- Uncertainties.

Handle:
- Missing API keys.
- Rate limits.
- Network failures.
- Timeouts.
- Provider unavailability.
- Invalid JSON.
- Unsupported claims.
- Empty responses.

When the LLM is unavailable, continue to report actual deterministic findings collected from Kubernetes.

Clearly indicate that AI reasoning was unavailable. Never pretend an LLM call succeeded.

The technical-only scope guard must work even when the AI provider is unavailable.

---

# 12. Safe, Read-Only Kubernetes Access

The MVP must investigate and recommend fixes, but must not automatically modify Kubernetes resources.

Do not:
- Delete pods.
- Restart workloads.
- Modify Deployments.
- Apply YAML.
- Change Services or networking rules.
- Execute arbitrary shell commands.
- Allow LLM-generated commands to run.

Create a dedicated Kubernetes ServiceAccount with minimal read-only RBAC permissions for the implemented features.

Add resource permissions only when genuinely required.

Test that unauthorized write operations are denied.

For each recommended fix, show:
- Proposed action.
- Why it is recommended.
- Supporting evidence.
- Potential risk.
- Expected impact.
- How to apply the fix manually.
- How to verify recovery.

Future remediation features must use explicit allowlisted actions, human approval, authorization checks, audit logging and verification.

Do not implement unrestricted command execution.

---

# 13. SQLite Persistence

Use SQLite to persist:
- Investigation ID.
- Start and completion times.
- Namespace and target resources.
- Investigation status.
- Evidence records or appropriately redacted references.
- Detected conditions.
- Root-cause assessment.
- Recommendations.
- AI provider status.
- Verification status.
- Investigation duration.
- Errors and failure reasons.

Implement real database reads and writes.

History must survive backend restarts.

Do not implement hosted authentication or InsForge in the initial local single-user release. Keep the design extensible if multi-user support is needed later.

---

# 14. Real-Time Progress with SSE

Use Server-Sent Events.

Emit progress only when the corresponding backend operation actually occurs.

Possible stages:
- Investigation created.
- Kubernetes connectivity checked.
- Resources collected.
- Logs collected.
- Events analyzed.
- Diagnostic rules evaluated.
- AI analysis requested.
- Findings validated.
- Report persisted.
- Investigation completed or failed.

These stages must represent actual execution, not a timer-driven animation.

Handle client disconnects, reconnects, invalid IDs, failed collectors, backend restarts and final status retrieval.

The frontend must display genuine failure states rather than a permanent loading indicator or false success.

---

# 15. Mandatory Testing Strategy

Keep testing practical and proportional to the project.

## A. Unit tests — Mandatory

Use pytest.

Test:
- Diagnostic rules.
- Technical-topic filtering.
- Off-topic rejection.
- Evidence normalization.
- Response validation.
- Evidence-strength logic.
- Secret redaction.
- Error handling.
- Malformed AI responses.
- Unsupported evidence references.

Unit tests must be fast and must not require a running Kubernetes cluster or external LLM.

## B. Integration tests — Mandatory

Test interactions between:
- FastAPI and the investigation engine.
- Kubernetes client and a real local cluster.
- SQLite persistence.
- SSE progress delivery.
- Diagnostic logic and evidence collection.
- AI provider adapter and response validation.

Use controlled test doubles only for isolated dependency failures, such as an unavailable external API.

Clearly distinguish mocked tests from tests that use a real cluster.

## C. End-to-end test — Mandatory

At minimum:

1. Deploy a test workload to kind.
2. Introduce a genuine failure.
3. Open the React dashboard.
4. Click Investigate Cluster.
5. Confirm actual Kubernetes evidence is collected.
6. Confirm the agent identifies the likely cause or correctly reports insufficient evidence.
7. Review the recommended fix.
8. Apply the fix manually.
9. Run another investigation.
10. Verify actual recovery.
11. Confirm both investigations are saved.
12. Confirm monitoring metrics reflect the real executions.

Start with one reliable end-to-end scenario and expand to all five.

## D. Security tests — Mandatory

Test:
- Off-topic requests are rejected.
- Ambiguous requests are handled safely.
- Prompt injection in logs does not override application policy.
- Secret values are not leaked.
- Unauthorized Kubernetes writes are denied.
- Malformed requests are rejected.
- AI output cannot bypass validation.
- Oversized inputs and logs are bounded.
- Errors do not expose credentials.

## Independent failure suite

Create real Kubernetes failure scenarios for all five supported categories.

Expected answers may exist in the independent test suite but must never be passed to the production agent as hidden hints.

Test:
- Healthy workload.
- CrashLoopBackOff.
- ImagePullBackOff.
- Pending Pods.
- Service Selector Mismatch.
- Readiness/Liveness Probe Failures.
- Insufficient evidence.
- Kubernetes API unavailable.
- Missing permissions.
- AI unavailable.
- Malformed AI output.

Compare actual findings against independently stored expected outcomes.

Do not weaken assertions merely to obtain a passing test report.

Keep cluster-dependent tests separate from fast tests where necessary.

Load testing and advanced performance testing are optional for the initial release.

---

# 16. Docker and Jenkins CI/CD — Jenkins Only

**Jenkins is the sole CI/CD orchestration tool for this project. Do not implement GitHub Actions.**

Use Git and GitHub as the source-code repository, but all automated pipeline orchestration must be performed by Jenkins.

## Jenkins implementation

Create a real Declarative `Jenkinsfile` in the project root and commit it to the repository.

The pipeline must perform actual operations:

1. Check out the actual source from Git.
2. Install or restore backend dependencies reproducibly.
3. Run backend linting and formatting checks.
4. Run backend unit tests.
5. Run frontend linting and tests.
6. Build the React application.
7. Build the backend and frontend Docker images.
8. Validate Kubernetes manifests.
9. Run Trivy scans against applicable images and dependencies.
10. Publish genuine test reports and scan results.
11. Report failures accurately.

Configure meaningful stages, timeouts, error handling and post-build actions.

A failed required command must fail the pipeline. Do not suppress failures just to produce a green build.

Do not make the pipeline dependent on a paid LLM API.

## Jenkins environment

Run Jenkins locally in Docker where practical, subject to available laptop resources.

Document:
- Jenkins setup.
- Required plugins.
- Agent configuration.
- Tool versions.
- Credential configuration.
- Git repository connection.
- Pipeline creation.
- Build execution.
- Troubleshooting.

Prefer a reproducible Jenkins agent environment containing the required Python, Node.js, Git, Docker build capability and Trivy tooling.

Do not assume that a Jenkins container automatically has access to the host Docker daemon or Kubernetes cluster.

Configure and verify these connections explicitly.

Do not mount the host Docker socket or grant privileged access without explaining the security implications. Prefer a safer, reproducible image-building approach when practical.

Cluster-dependent integration and end-to-end tests may run in a separate Jenkins stage or job that requires an explicitly configured test cluster.

Document Windows and Docker Desktop limitations. Verify the pipeline on the actual supported environment.

## Jenkins acceptance criteria

- Jenkinsfile is committed to Git.
- Jenkins checks out the real project.
- Backend and frontend checks execute.
- Tests execute and produce actual reports.
- Docker images build successfully.
- Kubernetes manifests are validated.
- Trivy scans execute and produce genuine results.
- Failed required stages fail the pipeline.
- Credentials are not exposed in logs.
- The pipeline has been executed and its actual outcome is documented.

Do not claim Jenkins integration is complete until these conditions have been verified.

---

# 17. Prometheus and Grafana

Instrument the real backend with Prometheus metrics, including:
- Total investigations.
- Successful and failed investigations.
- Investigation duration.
- Evidence collector failures.
- LLM request duration.
- LLM request errors.
- LLM fallback count.
- Diagnostic outcomes.

Avoid high-cardinality metric labels. Do not use pod names, investigation IDs, user identifiers, secrets or raw log content as metric labels.

Build Grafana dashboards backed by the actual Prometheus endpoint.

Verify that metrics change after real investigations.

Do not hardcode dashboard values or fabricate a monitoring demonstration.

---

# 18. Final Repository Structure

Use a maintainable monorepo:

ai-kubernetes-troubleshooting-agent/
- backend/
  - app/
    - api/
    - core/
    - kubernetes/
    - collectors/
    - diagnostics/
    - agent/
    - models/
    - services/
    - storage/
    - observability/
  - tests/
- frontend/
  - src/
    - components/
    - pages/
    - services/
    - hooks/
- k8s/
  - base/
  - scenarios/
  - rbac/
- monitoring/
  - prometheus/
  - grafana/
- scripts/
- docs/
- Jenkinsfile
- docker-compose.yml
- .env.example
- .gitignore
- README.md

Adapt the structure when there is a justified technical reason.

Do not create unnecessary empty folders or placeholder modules.

Keep production code, test fixtures, expected diagnoses, Kubernetes manifests, and operational scripts appropriately separated.

---

# 19. Required Milestones

Complete the milestones in order.

## M0 — Environment and Foundation

Inspect the actual Windows environment, Docker Desktop, Python, Node.js, Git, available resources and project directories.

Create the new repository and scaffold React and FastAPI.

Acceptance:
- Both applications start locally.
- Backend health endpoint works.
- Git hygiene is correct.
- Prerequisites for kind have been verified.

## M1 — Local Kubernetes Connectivity

Set up kind, verify cluster health, deploy a healthy sample workload and connect FastAPI to the actual cluster.

Acceptance: the backend returns real Kubernetes resources.

## M2 — Evidence Collection

Implement real collectors for the resources needed by the five supported scenarios.

Acceptance: evidence includes resource identity, timestamps, source and collection status.

## M3 — Diagnostic Engine

Implement five evidence-based diagnostic categories and unit tests.

Acceptance: diagnostic rules work, healthy workloads are recognized, and ambiguous conditions are handled honestly.

## M4 — AI Integration

Implement the provider interface, OpenRouter configuration, evidence sanitization, structured responses, validation and deterministic fallback.

Acceptance: AI results are validated, and the application remains useful when the provider is unavailable.

## M5 — React Dashboard

Implement the real investigation workflow, SSE progress, evidence viewer, diagnosis report and history.

Acceptance: a user can investigate the real cluster from the UI and retrieve persisted results.

## M6 — Failure Simulation and Recovery Verification

Create reproducible failure scenarios for all five supported categories.

Acceptance: the agent is tested against actual cluster failures, with independently evaluated expected results.

## M7 — Security and Testing

Complete mandatory unit, integration, end-to-end and security tests.

Verify least-privilege RBAC and secret handling.

Acceptance: tests pass and unauthorized write operations are denied.

## M8 — Docker and Jenkins CI/CD

Build actual Docker images and implement a Jenkins Declarative Pipeline.

Acceptance: Jenkins checks out the repository, runs tests and builds, validates Kubernetes manifests, runs Trivy scans, publishes genuine results and fails correctly when required stages fail.

## M9 — Monitoring

Configure actual Prometheus metrics and Grafana dashboards.

Acceptance: dashboards reflect real investigation activity.

## M10 — Final Acceptance and Portfolio

Run the complete end-to-end demonstration, document limitations, capture genuine screenshots, and prepare interview materials.

Acceptance: another developer can follow the README, create a test failure, investigate it and verify recovery.

Do not claim a milestone is complete without its required evidence and tests.

---

# 20. Working Method — Follow Strictly

I am building this on Windows using VS Code and PowerShell, and I am still learning several DevOps technologies.

At the beginning:

1. Inspect my actual environment and project directories.
2. Identify installed tools and versions.
3. Check Docker Desktop status and available resources.
4. Determine whether kind is feasible.
5. Explain the minimum setup required for M0.

For every milestone:

1. Explain the objective in simple language.
2. List the files to create or modify.
3. Provide exact PowerShell commands in the correct order.
4. Provide complete file contents when creating a file.
5. Inspect existing files before replacing them.
6. Explain important commands and technical decisions.
7. Run or ask me to run the relevant tests.
8. Inspect actual output.
9. Fix errors before continuing.
10. Confirm acceptance criteria before moving to the next milestone.

Do not dump the entire codebase at once.

Do not overwrite my existing DBGuardian or Terraform AWS DevOps projects. This must be a separate repository.

Do not assume that a tool is installed or that Docker is running.

Do not install unnecessary packages or services.

Do not weaken tests, fabricate success, or substitute fake implementations when real implementation becomes difficult.

If a command fails, investigate the actual error rather than applying unrelated fixes.

If a feature is incomplete, explicitly mark it incomplete.

Never claim that you executed commands on my machine if you did not.

Use stable, compatible dependencies and reproducible setup instructions. Explain significant resource requirements before installing additional tools.

---

# 21. Final Acceptance Criteria

The project is complete only when these outcomes have been demonstrated:

- Real Kubernetes connectivity.
- Actual evidence collection.
- Five implemented and reproducible troubleshooting scenarios.
- Evidence-backed root-cause analysis.
- Honest uncertainty and no arbitrary confidence claims.
- Technical-only AI Q&A.
- Rejection of unrelated questions.
- Safe handling of prompt injection and sensitive data.
- Real LLM integration when available.
- Functional deterministic fallback.
- Working React-to-FastAPI investigation workflow.
- Genuine live progress updates.
- Persistent investigation history.
- Read-only Kubernetes permissions.
- Manual remediation and recovery verification.
- Passing unit, integration, end-to-end and security tests.
- Real Docker builds.
- Working Jenkins Declarative Pipeline.
- Actual Trivy scans.
- Real Prometheus metrics and Grafana dashboards.
- No exposed credentials.
- Complete README and setup instructions.
- Repeatable recruiter demonstration.

## Final Deliverables

At completion, provide:
1. Working source code.
2. Windows and PowerShell setup guide.
3. Architecture diagram.
4. Kubernetes manifests and RBAC configuration.
5. Five reproducible failure scenarios and cleanup procedures.
6. Automated tests and actual test results.
7. Docker configuration.
8. Jenkinsfile and Jenkins setup instructions.
9. Genuine Jenkins test, build and security scan results.
10. Prometheus configuration and Grafana dashboard.
11. Genuine screenshots from the running application.
12. Recruiter demonstration script.
13. Technical interview questions and explanations.
14. Known limitations and possible future extensions.

The project must be explainable by me, not merely generated by AI.

---

# START NOW — MILESTONE 0 ONLY

Begin by inspecting my actual development environment.

Determine:
- Installed Python, Node.js, npm, Git and Docker versions.
- Whether Docker Desktop is running.
- Available memory and disk space.
- Whether kind and kubectl are installed.
- Whether any existing directory would conflict with the new project.

Explain each command and its expected output.



Do not begin M1 until M0 has been verified.

## Governing Principles

**Real implementation. Real Kubernetes evidence. Technical-only AI. No fabricated results. Five thoroughly tested scenarios. Safe read-only operations. ₹0 local-first infrastructure target. Jenkins as the sole CI/CD orchestrator. Prometheus and Grafana backed by real data. Milestone-by-milestone verification.**
