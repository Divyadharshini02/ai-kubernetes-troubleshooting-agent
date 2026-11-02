# Milestone 6: Failure Scenarios and Recovery Verification

**Goal:** create reproducible failures for all five supported categories, and show the full lifecycle: introduce a failure, collect evidence, diagnose, fix it by hand, verify recovery.

**Acceptance criterion:** the agent is tested against actual cluster failures, with independently evaluated expected results.

**Status:** completed.

## Overview

```text
k8s/scenarios/healthy.yaml        namespace, Deployment and Service: the baseline and the fix
k8s/scenarios/<fault>.yaml        one fault, applied over the baseline
    -> real Kubernetes failure
    -> dashboard investigation    evidence and rules from Milestones 2 and 3, unchanged
    -> you apply healthy.yaml     the application never changes the cluster
    -> fresh investigation        compared with the previous one
```

Everything runs in the `failure-demo` namespace, with one pod limited to 64 MiB. The Deployment uses `strategy: Recreate`, so a fault really takes the app down. With a rolling update, the old pod would keep serving and hide it.

Deployment rollout failure is not a scenario. Rollout data is collected, but no rule diagnoses a stuck rollout, and the project supports exactly five categories.

## Prerequisites

- The kind cluster from [Milestone 1](M1_LOCAL_KUBERNETES.md), with the context `kind-troubleshooting-lab`.
- The backend and frontend running, as described in the [README](../README.md). A backend started before Milestone 6 must be restarted to pick up the new code.
- For the live tests, the two settings described in [Milestone 7](M7_RBAC_SECURITY.md#run-the-tests).
- No LLM or API key.

Every command below names the context, so it cannot reach another cluster by accident.

## Run a scenario

**Apply one file at a time.** Do not apply the whole `k8s/scenarios` folder, because that applies every fault.

Run all commands from the repository root. Start with the healthy baseline:

```powershell
kubectl --context kind-troubleshooting-lab apply -f k8s/scenarios/healthy.yaml
kubectl --context kind-troubleshooting-lab rollout status deployment/demo-web -n failure-demo
```

Then apply one fault:

```powershell
kubectl --context kind-troubleshooting-lab apply -f k8s/scenarios/image-pull.yaml
```

The other faults are `crash-loop.yaml`, `pending-pod.yaml`, `readiness-probe.yaml` and `service-selector.yaml`.

**Wait until `kubectl` shows the symptom** before investigating:

```powershell
kubectl --context kind-troubleshooting-lab get pods -n failure-demo
```

An investigation run during the first seconds describes a moment in the middle of the change, not the fault. See [Limitations](#limitations).

To investigate, open <http://localhost:5173>, enter `failure-demo` and click **Investigate Cluster**.

## Expected results

| Fault | Wait for | Main diagnosis | Evidence strength | Possible cause | Key evidence |
|---|---|---|---|---|---|
| `crash-loop.yaml` | At least 2 restarts, about 30 seconds | Container web is restarting repeatedly | Medium | May be missing required configuration | Exit code 3, and the log line about `DATABASE_URL` |
| `image-pull.yaml` | `ErrImagePull` or `ImagePullBackOff` | Container web cannot pull its image | High | May be unable to reach the registry, or the name may be wrong | Container state and the pull failure event |
| `pending-pod.yaml` | `Pending` | Pod … cannot be scheduled | High | The node selector may not match any node | `PodScheduled=False`, `FailedScheduling`, node labels |
| `readiness-probe.yaml` | Running, `0/1` ready, about 15 seconds | Readiness probe of container web is failing | High | The probe path may be wrong, because the application answered HTTP 404 | `Unhealthy` events with status code 404 |
| `service-selector.yaml` | Pod `1/1` ready, a few seconds | Service demo-web selector matches no pods | High | The selector may contain a typo | The selector, the pod's labels, no ready endpoints |

The first four also report **Service demo-web matches pods, but none are ready**. This is correct: taking the only pod out of service leaves the Service with no endpoints. These results are also asserted by the live tests.

A healthy namespace reports **No problems found**, which means no supported problem was detected. It does not prove the application works.

## Restore the healthy baseline

The fix is the same for every scenario, and you apply it yourself:

```powershell
kubectl --context kind-troubleshooting-lab apply -f k8s/scenarios/healthy.yaml
kubectl --context kind-troubleshooting-lab rollout status deployment/demo-web -n failure-demo
```

Then run a new investigation from the dashboard. It collects fresh evidence.

## How recovery verification works

When an investigation completes, its outcome is compared with the latest earlier **conclusive** investigation of the same namespace, and the result is saved with it:

| Previous conclusive result | This result | Status shown |
|---|---|---|
| Problems found | Problems found | **Still failing** |
| Problems found | No problems found, complete evidence, at least one pod | **Recovered** |
| Problems found | Inconclusive, partial evidence, or no pods | Not verified |
| No problems found, or none at all | Anything | Not verified |

- **Skipped runs:** failed, interrupted and inconclusive investigations are never compared with, because they show nothing about whether a problem was present. Investigating too soon after a fix therefore does not lose the comparison.
- **Deleted workloads:** deleting a failing workload leaves nothing to diagnose, so it shows Not verified, not Recovered.
- **What Recovered means:** the supported problems found earlier are no longer detected. It does not mean the application or cluster is fully healthy, and the dashboard says so.
- **What it compares:** outcomes, not individual problems. Pod names change after a fix, so individual diagnoses cannot be matched reliably.
- **Diagnoses are unaffected:** this is a separate field. It reads results and never changes them.

The status appears in the report and, for Recovered and Still failing, in History. Investigations saved before Milestone 6 stay Not verified.

## Clean up

First check that the namespace is the one these scenarios created:

```powershell
kubectl --context kind-troubleshooting-lab get namespace failure-demo --show-labels
```

It must show `app.kubernetes.io/managed-by=failure-scenarios`. If it does not, stop: something else owns it. Otherwise:

```powershell
kubectl --context kind-troubleshooting-lab delete namespace failure-demo
```

This removes only `failure-demo`. No scenario creates cluster-level resources or changes the node. Saved investigations remain in history.

The tests apply the same guards automatically, in [failure_scenarios.py](../backend/tests/failure_scenarios.py). Before writing anything, they check the context and the ownership label, and they accept only the named manifest files.

## Limitations

- **Investigating too early can mislead.** While a fault is being applied, the old pod is gone and the new one may not exist yet. In a real run, an investigation 2 seconds after applying a fault reported "Service demo-web selector matches no pods", and the next one reported the pod as unhealthy in an unexplained way. Both were true at that moment, but neither described the fault. This is Milestone 3 behaviour, which was left unchanged, so wait for the symptom in `kubectl`.
- **A crash loop needs time.** One restart is deliberately reported as insufficient evidence. It needs at least two.
- **Recovery compares outcomes.** If one fault is replaced by a different one, the status is Still failing, not "old problem fixed, new problem found".
- **One known test issue:** the `liveness-demo` test from [Milestone 4](M4_LLM_INTEGRATION.md#final-verification-results) remains timing-sensitive. Liveness probes are left out of these scenarios for this reason, so liveness failures are covered only by that test.
- **Not secured:** there is no login, so this is for local use only. Kubernetes access is read-only since [Milestone 7](M7_RBAC_SECURITY.md).

## Why the readiness probe waits

The manifests set `initialDelaySeconds: 10` on the readiness probe. In measurements, nginx took 6 to 8 seconds to answer on this machine. Without the delay, a normal start recorded up to 3 failed probes, even when idle, and the Milestone 3 rule correctly reports 3 recent failures as "may be unstable" for up to 10 minutes.

| Measured over 12 restarts (4 idle, 8 under load) | Restarts with 3 or more failures | Most failures in one restart |
|---|---|---|
| Without the delay | 6 of 12 | 3 |
| With the delay | 0 of 12 | 1 |

The rule itself was not changed, so a pod that really keeps failing is still reported. The delay only moves the first check, and `readiness-probe.yaml` keeps the same delay with its broken path, so its failures are the intended HTTP 404. The cost is that the pod takes about 5 seconds longer to become ready.

## Troubleshooting

| Symptom | Likely reason | What to do |
|---|---|---|
| `namespaces "failure-demo" not found` | The baseline was not applied | Apply `healthy.yaml` first |
| A fault shows an unexpected diagnosis | Investigated too early | Wait for the symptom in `kubectl`, then investigate again |
| A healthy pod shows "Readiness of container web may be unstable" | The pod took over about 20 seconds to start, so its readiness probe failed 3 or more times. This should now be rare, because the probe waits 10 seconds first | It clears within 10 minutes. Or close heavy applications and start a new pod with `kubectl --context kind-troubleshooting-lab rollout restart deployment/demo-web -n failure-demo`. See [why the readiness probe waits](#why-the-readiness-probe-waits) |
| The dashboard reports that no restricted credentials are configured | `KUBECONFIG` is not set for the backend | Follow [Milestone 7 set-up](M7_RBAC_SECURITY.md#set-up) |
| Status stays Not verified after a fix | Investigated during the rollout, or no earlier run found problems | Wait for the rollout, then investigate again. An earlier run must have found problems |
| The dashboard shows no recovery status | The backend was started before this milestone | Restart the backend |
| Dashboard reports the cluster unavailable | Docker Desktop or kind is not running | See [Milestone 1](M1_LOCAL_KUBERNETES.md) |

## Interview walkthrough

Allow about five minutes. `image-pull.yaml` is the most reliable fault for a live demonstration: it is quick, needs no internet and gives a high-strength result. Use `service-selector.yaml` as a backup.

1. **Explain the goal.** "It diagnoses real failures using evidence and deterministic rules. It suggests fixes but never applies them."
2. **Deploy the healthy app**, using the baseline commands above.
3. **Investigate `failure-demo`.** Show **No problems found**, and explain that it only means no supported problem was detected.
4. **Introduce the fault** with `image-pull.yaml`. Run `kubectl get pods -n failure-demo` until it shows `ErrImagePull` or `ImagePullBackOff`.
5. **Investigate again.** Show the live progress, then the diagnosis, evidence strength and possible causes.
6. **Open a recommended fix.** Point out that it is manual.
7. **Open Evidence.** Show the record behind the diagnosis.
8. **Investigate once more** without fixing it, and show **Still failing**.
9. **Fix it by hand** with the restore commands, and wait for the rollout.
10. **Investigate a last time.** Show **Recovered**, and read out that this does not prove the application is fully healthy.
11. **Compare runs in History** by clicking through the entries.
12. **Clean up** with the commands above.

## Run the tests

From `backend/`:

```powershell
# No cluster needed: recovery logic, manifest checks and cleanup guards
.\.venv\Scripts\python.exe -m pytest tests\test_recovery_verification.py

# Real cluster: every scenario in turn, about 2 minutes
.\.venv\Scripts\python.exe -m pytest tests\test_failure_scenarios_live.py -m cluster
```

Each live scenario starts healthy, applies the fault, confirms it directly with `kubectl`, checks the diagnosis, applies the fix, and checks that a fresh investigation shows Recovered. The tests use a temporary database, never the real one.

## Final verification results

Recorded on 2026-11-02, on a local kind cluster, with no real provider calls.

| Check | Result |
|---|---|
| Non-cluster suite | 893 passed, 56 deselected |
| Real-cluster suite, run once | 56 passed, 893 deselected |
| `npm run lint` | No problems |
| `npm run build` | Succeeded |
| Server-side dry run of every manifest | Accepted |
| After the runs | `failure-demo` removed, and the `sample-app` pod unchanged with 0 restarts |

All five faults produced the expected diagnoses above on the first run, with no rule or expectation changed.

**Browser run:** real headless Chrome, driven by a script, through the dashboard, using `image-pull.yaml`. The four investigations showed Not verified, Not verified, Still failing and Recovered, in both the report and History where applicable, with no page errors.
