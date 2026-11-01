# Milestone 3: Deterministic Diagnostics

**Goal:** turn collected evidence into diagnoses for the five supported scenarios, using rules a person can read and check.

**Acceptance criterion:** diagnostic rules work, healthy workloads are recognized, and ambiguous conditions are handled honestly.

**Status:** completed and approved. There is no LLM in this milestone. The same evidence always produces the same diagnoses. Fixes are suggested and never applied. [Milestone 4](M4_LLM_INTEGRATION.md) adds optional explanations without changing any of this.

## Architecture

| Part | File | Job |
|---|---|---|
| Evidence index | [app/diagnostics/index.py](../backend/app/diagnostics/index.py) | Read-only lookups over one evidence bundle |
| Shared helpers | [app/diagnostics/common.py](../backend/app/diagnostics/common.py) | Builds diagnoses, and rejects citations of evidence that does not exist |
| Rules | [app/diagnostics/rules/](../backend/app/diagnostics/rules/) | One module per scenario |
| Engine | [app/diagnostics/engine.py](../backend/app/diagnostics/engine.py) | Runs the rules, isolates failures and decides the outcome |
| API | [app/api/diagnosis.py](../backend/app/api/diagnosis.py) | Collects evidence with the Milestone 2 collectors, then runs the engine |

Rules receive evidence only. They cannot call Kubernetes, so diagnosis itself cannot change the cluster.

Each rule declares what it needs:

- **Required evidence:** without it, the rule does not run and reports `insufficient_evidence`.
- **Supporting evidence:** without it, the rule runs and reports `partial`.

## The API

```text
GET /api/namespaces/{namespace}/diagnosis
GET /api/namespaces/{namespace}/diagnosis?include_evidence=true
```

Each call collects fresh evidence. With `include_evidence=true`, the response also contains the full evidence bundle, so every citation can be followed to its record.

| Situation | HTTP status | Result |
|---|---|---|
| Diagnosis ran | 200 | A report with an `outcome` |
| Namespace does not exist | 404 | `namespace_not_found` |
| Invalid namespace name | 422 | Rejected before any cluster call |
| Kubeconfig missing or invalid | 503 | `kubeconfig_unavailable` |
| API server unreachable, or the TLS connection failed | 503 | `cluster_unreachable` |

### Outcomes

| `outcome` | Meaning |
|---|---|
| `problems_found` | At least one problem is confirmed |
| `insufficient_evidence` | Nothing is confirmed, but evidence was missing, conflicting or unexplained |
| `no_problems_found` | Every rule had all its evidence and found nothing |

`no_problems_found` means no supported problem appears in the collected evidence. It does not prove the application works.

### Example: healthy namespace

Actual response for `sample-app`, with the rule list shortened:

```json
{
  "namespace": "sample-app",
  "context": "kind-troubleshooting-lab",
  "outcome": "no_problems_found",
  "summary": "No supported problem was detected in the collected evidence. Examined 1 pod, 1 container and 1 service.",
  "evidence_completeness": "complete",
  "resources_examined": {"pods": 1, "containers": 1, "services": 1},
  "diagnoses": [],
  "rule_results": [
    {"rule": "crash_loop", "status": "evaluated", "diagnoses": 0, "missing_evidence": []}
  ],
  "evidence": null
}
```

### Example: one diagnosis

From a real out-of-memory failure in the test lab, shortened to one item per list:

```json
{
  "diagnosis_id": "crash_loop_backoff:evidence-lab/oom-demo/app",
  "scenario": "crash_loop_backoff",
  "title": "Container app is restarting repeatedly",
  "status": "problem_detected",
  "evidence_strength": "high",
  "affected_resource": {"kind": "Pod", "name": "oom-demo", "namespace": "evidence-lab", "container": "app"},
  "explanation": "Container app in pod oom-demo has restarted 4 times and is still crashing or restarting. Possible causes below are inferences from the evidence.",
  "confirmed_facts": [
    {
      "statement": "The termination before that had exit code 137 and reason OOMKilled.",
      "evidence_ids": ["container-last-state:evidence-lab/oom-demo/app"],
      "observed_at": "2026-11-01T15:20:39Z",
      "excerpt": null
    }
  ],
  "possible_causes": [
    {"statement": "The container exceeded its memory limit.", "evidence_ids": ["container:evidence-lab/oom-demo/app"]}
  ],
  "missing_evidence": [],
  "conflicting_evidence": [],
  "recommendations": [
    {
      "action": "Review the memory limit of container app and the application's memory use.",
      "reason": "The container was terminated with reason OOMKilled.",
      "risk": "A higher limit uses more node memory. A memory leak would still recur.",
      "expected_impact": "The container stops being killed if the limit was too low.",
      "how_to_apply": ["Change resources.limits.memory in the workload manifest, then apply it yourself."],
      "how_to_verify": ["kubectl get pod oom-demo -n evidence-lab --watch", "Run this diagnosis again and confirm the restart count stops rising."],
      "evidence_ids": ["container:evidence-lab/oom-demo/app"]
    }
  ],
  "evidence_references": [
    {
      "evidence_id": "container-last-state:evidence-lab/oom-demo/app",
      "category": "container_last_state",
      "resource_kind": "Container",
      "resource_name": "app",
      "temporality": "historical",
      "status": "collected",
      "collected_at": "2026-11-01T15:21:39.133454Z",
      "observed_at": "2026-11-01T15:20:39Z",
      "source": "GET /api/v1/namespaces/evidence-lab/pods"
    }
  ]
}
```

## How to read a diagnosis

| Field | Meaning |
|---|---|
| `confirmed_facts` | What the evidence directly shows |
| `excerpt` | Text quoted from logs or Kubernetes, limited to 300 characters. It is untrusted data |
| `possible_causes` | Inferences. They may be wrong, and more than one can apply |
| `missing_evidence` | What could not be checked |
| `conflicting_evidence` | Where evidence disagrees |
| `recommendations` | Manual suggestions with reason, risk, expected impact, how to apply and how to verify |
| `evidence_references` | Each cited record, with its timestamps, source and whether it is current or historical |

Every fact, cause and recommendation cites evidence. A rule that cites evidence that was not collected fails, and is reported as an `error`.

| `status` | Meaning |
|---|---|
| `problem_detected` | Current evidence confirms the problem. The cause may still be uncertain |
| `insufficient_evidence` | Something looks wrong, but the evidence cannot support a conclusion |

### Evidence strength

This rates how well the evidence supports the leading possible cause. It is a classification, not a probability, and no percentages are used.

| `evidence_strength` | Meaning |
|---|---|
| `high` | Multiple observations support a specific cause, or Kubernetes states it directly |
| `medium` | Evidence supports a cause, but alternatives remain |
| `low` | The problem is confirmed, but nothing identifies why |
| `insufficient` | The evidence does not support a conclusion |

## The rules

### What counts as current

Current state decides whether a problem exists. Events and last terminations are historical, so they only count as active within **10 minutes** of collection. Container restart back-off reaches at most five minutes, so an active crash loop keeps producing newer evidence than that.

### CrashLoopBackOff

| Evidence | Result |
|---|---|
| Waiting with reason `CrashLoopBackOff` | Problem |
| Terminated with a non-zero exit code, with at least one restart | Problem |
| Terminated with exit code 0, with at least one restart and a recent back-off event | Problem |
| At least 2 restarts, and a recent termination or back-off event, even if running and ready | Problem |
| 1 restart with a recent termination or back-off event | Insufficient: one restart is not a loop |
| Restarts, but the last termination and back-off events are old | Not flagged |

Running and ready does not rule this out. On a real cluster, crash-looping containers were observed as running and ready just after each restart.

| Strength | When |
|---|---|
| High | `OOMKilled`, or recent liveness failures |
| Medium | A known exit code (126, 127, 137, 143) or a matching log line |
| Low | The crash is confirmed, but nothing identifies why |

Log lines are matched against patterns for missing configuration, dependency failures and permissions. A generic error line is only used when nothing better is known, because a failing probe's own request can appear in the logs as an error.

### ImagePullBackOff

| Evidence | Result |
|---|---|
| Waiting with reason `ImagePullBackOff`, `ErrImagePull`, `InvalidImageName` or `ErrImageNeverPull` | Problem |
| Any other state, including running with old pull failure events | Not flagged |

The cause comes from the current status message and recent `Failed to pull` events. Old events are not used, because the image may have changed since, and they are reported as conflicting evidence.

| Strength | When |
|---|---|
| High | A clear error: not found, unauthorized, rate limited, network or DNS, or a malformed reference |
| Medium | `pull access denied`, which registries use for both missing images and missing credentials |
| Low | No error detail is available |

### Pending pods

| Evidence | Result |
|---|---|
| Pending with `PodScheduled=False` | Problem |
| Pending with no node and no `PodScheduled` condition | Insufficient: the pod may be very new |
| `PodScheduled=False`, but a node is assigned | Insufficient: conflicting |
| Pending and already scheduled | Not flagged here: this belongs to other rules |

The condition is what decides, and the age of events does not matter. On a real cluster, the scheduler emitted `FailedScheduling` once and the event simply aged while the pod stayed Pending.

Each blocker the scheduler names is then checked against collected configuration:

| Scheduler message | Check |
|---|---|
| Node affinity or selector | Node selector compared with real node labels |
| Insufficient CPU, memory or ephemeral storage | Total requests compared with the largest node's allocatable amount |
| Untolerated taint | Node taints compared with pod tolerations |
| Unbound or missing volume claim | Claim exists, its phase, and whether its StorageClass exists |
| Pod affinity or anti-affinity | Pod defines those rules |
| Unschedulable nodes | Node is marked unschedulable |
| No nodes available | Node list is empty |

| Strength | When |
|---|---|
| High | Collected configuration independently agrees with the scheduler |
| Medium | Only the scheduler's message supports it, or configuration could not be checked |
| Low | The message matches no known blocker |

If the scheduler's message and the configuration disagree, the disagreement is reported as conflicting evidence.

### Service selector mismatch

The rule compares the real selector with real pod labels, because not every Service without endpoints has a mismatch. Services with no selector and `ExternalName` Services are skipped.

| Evidence | Title or result | Strength |
|---|---|---|
| No pod matches, a similar pod exists, endpoints are empty | Selector matches no pods | High |
| No pod matches, no similar pod exists | Selector matches no pods | Medium |
| No pod matches, EndpointSlices unavailable | Selector matches no pods | Low |
| A matching Deployment is scaled to zero | Selects a workload with no pods | Medium |
| Pods match, none are ready | Matches pods, but none are ready. This is not a mismatch | High |
| A named `targetPort` is declared by no matching pod | Targets a port name its pods do not declare | High |
| Pod list truncated, no match found | Insufficient: a match may exist | Insufficient |
| No pod matches, but endpoints are ready | Insufficient: conflicting | Insufficient |
| Ready pods match, but no endpoint is ready | Insufficient: conflicting | Insufficient |
| Pods match and endpoints are ready | Not flagged | Not applicable |

A numeric `targetPort` not declared on a container is not flagged, because Kubernetes does not require it to be declared.

### Probe failures

Readiness, liveness and startup probes are evaluated and reported separately. The failure type comes from the event message.

| Probe | Evidence | Result |
|---|---|---|
| Readiness | Running, not ready, with recent failures | Problem |
| Readiness | Running, not ready, with no recent failure event | Insufficient: it may still be starting |
| Readiness | Ready now, with 3 or more recent failures | Insufficient: conflicting |
| Readiness | Ready now, with fewer than 3 recent failures | Not flagged: normal during start-up |
| Liveness | Recent failures, with a restart or `Killing` event, or 3 or more failures | Problem |
| Liveness | Recent failures, with no liveness probe configured | Insufficient: conflicting |
| Startup | Not started, with recent failures | Problem |
| Any | Only old failures on a healthy container | Not flagged |

The latest failure message decides the cause, because an early `connection refused` during start-up can be followed by the real failure.

| Strength | When |
|---|---|
| High | The failure message identifies a cause, or the probe port disagrees with declared ports |
| Medium | Failure confirmed, but the message is not recognised |

A container with a failing liveness probe usually gets two diagnoses: this one and CrashLoopBackOff. The crash diagnosis names the probe failure as a possible cause.

### Unsupported conditions

An unhealthy pod that no rule explains is reported with scenario `unclassified`, status `insufficient_evidence` and no possible causes. For example, a missing required Secret produces this. Without this rule, such a pod could wrongly produce `no_problems_found`.

Pods being deleted are ignored, because a pod shutting down is expected to look unhealthy.

## Run the tests

From `backend/`:

```powershell
# No cluster needed
.\.venv\Scripts\python.exe -m pytest

# Needs the kind cluster and the sample workload
.\.venv\Scripts\python.exe -m pytest -m cluster
```

Unit tests for each rule cover positive and negative matches, missing evidence, conflicting or stale evidence, and multiple simultaneous issues. They use messages captured from a real cluster where available.

The cluster tests create real failures from [backend/tests/fixtures/evidence-lab.yaml](../backend/tests/fixtures/evidence-lab.yaml) and compare the results with expectations in [test_diagnosis_live.py](../backend/tests/test_diagnosis_live.py). Those expectations are never available to production code.

## What was verified on a real cluster

| Verified on real failures | Verified with test data only |
|---|---|
| Crash loop with an application exit code | Exit codes 126, 127 and 143 |
| Out-of-memory kill | Log patterns for configuration, dependency and permission failures |
| Crash loop caused by a liveness probe | Init container crash loops |
| Image pull failure from DNS | Image not found, unauthorized and rate limited errors |
| Pending from node selector, CPU, anti-affinity and a missing StorageClass | Pending from taints, cordoned nodes and memory |
| Selector matching no pods | Scaled to zero, and a missing named port |
| Matching pods that are not ready | Conflicting Service evidence |
| Readiness and liveness HTTP 404 failures | Startup probes, and TCP, gRPC and exec probes |
| A missing required Secret as `unclassified` | Denied access, using a stub API rather than real RBAC |
| Healthy namespace | Truncated lists and time limits |

## Limitations

- **Unvalidated thresholds:** 10 minutes, 2 restarts and 3 failures are reasoned defaults. They have not been validated against a labelled dataset.
- **Message matching:** scheduler, kubelet and registry messages are not stable APIs, and matching was only tested with Kubernetes 1.37. A message that stops matching lowers the strength or gives no cause. It does not produce a wrong cause.
- **Pattern-based log matching:** this can match irrelevant lines, which is why these remain possible causes.
- **Single snapshot:** each call sees one moment. A problem that stopped more than 10 minutes ago is not reported, and a pod can change right after collection.
- **Event retention:** Kubernetes deletes old events, one hour by default, so a missing event does not prove nothing happened.
- **No fleet view:** diagnoses are per pod, so ten crashing replicas produce ten diagnoses.
- **Not evaluated:** node affinity expressions are not compared with node labels, and only exact Service selectors are supported, which is all Services have. Stuck terminating pods are ignored.
- **Node capacity:** "insufficient" compares requests with allocatable capacity. Usage by other pods is not collected, so a request that fits a node stays at medium strength.
- **Quoted text is untrusted:** `excerpt` fields can contain anything a workload wrote. They are redacted and bounded, and the rules never act on them. Milestone 4 must treat them as untrusted.
- **Redaction is best effort:** see [Milestone 2](M2_EVIDENCE_COLLECTION.md).
- **Admin credentials:** the backend still uses kind's default kubeconfig until the read-only ServiceAccount in Milestone 7.
- **Not yet built:** diagnoses are not saved, there is no screen for them, and recovery is checked by running the diagnosis again.
