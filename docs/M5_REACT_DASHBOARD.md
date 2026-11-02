# Milestone 5: React Dashboard

**Goal:** implement the real investigation workflow, SSE progress, evidence viewer, diagnosis report and history.

**Acceptance criterion:** a user can investigate the real cluster from the UI and retrieve persisted results.

**Status:** completed. The LLM has still not been run against the real OpenRouter service.

## User flow

1. The dashboard shows backend status, the cluster overview and saved investigations.
2. The user types or picks a namespace, optionally ticks **Include AI explanation**, and clicks **Investigate Cluster**.
3. The backend validates the request, saves a record and replies at once with its ID.
4. Progress appears as each stage really finishes.
5. The dashboard shows the diagnosis report and the evidence, and adds the investigation to History.
6. After fixing a problem by hand, the user runs a new investigation. The application never applies a fix.

## Request flow

```text
React dashboard
  -> POST /api/investigations              validate and save a running record
       -> background task
            -> read_cluster_health()       connectivity (Milestone 1)
            -> collect_namespace_evidence  evidence (Milestone 2)
            -> diagnose                    authoritative diagnosis (Milestone 3)
            -> explain_report              only if requested (Milestone 4)
            -> save the report and final events together
  -> GET /api/investigations/{id}/events   replays saved events, then follows new ones
  -> GET /api/investigations/{id}          the saved result
```

Milestones 1 to 4 are called, not changed. `GET /api/namespaces/{namespace}/diagnosis` works as before and saves nothing.

| File | Job |
|---|---|
| [app/models/investigation.py](../backend/app/models/investigation.py) | Request and response schemas |
| [app/storage/investigations.py](../backend/app/storage/investigations.py) | SQLite reads and writes |
| [app/services/investigations.py](../backend/app/services/investigations.py) | Runs the stages |
| [app/api/investigations.py](../backend/app/api/investigations.py) | Routes and the event stream |
| [frontend/src/App.jsx](../frontend/src/App.jsx) | Page state, following progress, status checks |
| [frontend/src/components/](../frontend/src/components/) | One component per dashboard section |

## The API

| Route | Result |
|---|---|
| `POST /api/investigations` | `202` with a running summary. Body: `{"namespace": "sample-app", "explain": false}`. Invalid input returns `422` and saves nothing |
| `GET /api/investigations?limit=50` | Saved summaries, newest first, up to 200 |
| `GET /api/investigations/{id}` | The summary, events and report, or `404` |
| `GET /api/investigations/{id}/events` | Server-Sent Events, or `404` |

If the database cannot be used, these routes return `503` with `storage_unavailable`. The rest of the API keeps working.

A problem during an investigation is not an HTTP error, because the request to start it has already succeeded. It becomes a saved `failed` status with a code such as `namespace_not_found`, `cluster_unreachable` or `interrupted`.

## Persistence

The database is `backend/data/investigations.db`, using Python's standard `sqlite3`. Git ignores it. It is created on first use, and each call opens its own connection, so background work and requests can use it at once.

**`investigations`** has one row per investigation:

| Column | Holds |
|---|---|
| `id` | A random ID |
| `namespace`, `explain_requested` | What was asked for |
| `status` | `running`, `completed` or `failed` |
| `verification_status` | Always `not_verified`, until Milestone 6 |
| `started_at`, `completed_at`, `duration_seconds` | Timing |
| `outcome`, `summary`, `diagnosis_count` | The result, for the history list |
| `ai_status`, `ai_reason` | The LLM result, empty if not requested |
| `error_code`, `error_message` | Why it failed |
| `report_json` | The full report, including diagnoses, recommendations and evidence |

**`progress_events`** has one row per stage: `investigation_id`, `sequence`, `stage`, `message` and `occurred_at`.

The report and its final events are written in one transaction, so a reader cannot see one without the other.

**After a restart:** saved investigations remain. At startup, anything still marked `running` is marked `failed` with the code `interrupted`, because nothing can really be running then. This assumes one backend process, which is how the project runs today.

**What is stored:** only evidence that was already redacted during collection, and fixed error messages. API keys and kubeconfig contents are never written. Redaction is best effort, so treat the file as containing cluster information. History has no size limit or delete option.

## Live progress

The stages are `created`, `connectivity_checked`, `evidence_collected`, `rules_evaluated`, `ai_requested`, `ai_finished`, `report_saved`, and then `completed` or `failed`. Each one is saved only after that work has happened. There are no timers or simulated steps. The two AI stages appear only when an explanation is requested.

**The stream** polls the saved events four times a second and sends new ones with their sequence as the event ID. It sends a keep-alive comment every 15 seconds, and ends with an `end` event once the status is final.

**If the connection drops:**

1. While the browser can retry, it reconnects by itself and sends the last ID it saw, so the stream resumes without repeats or gaps.
2. A new stream, such as after a page reload, replays from the start, and the dashboard ignores any sequence it already has.
3. The stream stops after 300 seconds with a `stream_limit` event, which is well above the collection and LLM limits combined.
4. The dashboard also reads the saved status every 5 seconds. If the backend is unreachable, it says the status is unknown and keeps trying, for up to 10 minutes. It then says no final status was received and points to History.

So the page cannot show a permanent spinner or a false success.

## Limitations

- **Coarse stages:** evidence collection is one stage, because per-collector progress would mean changing Milestone 2.
- **Possible causes, not a root cause:** the report uses Milestone 3's wording, which deliberately does not claim certainty.
- **No prevention guidance:** the diagnosis data does not contain it, and adding it would change Milestone 3.
- **AI status appears only after a run:** it comes from the report, so the dashboard does not know whether the LLM is enabled beforehand.
- **Namespace suggestions come from pods:** a namespace with no pods is not suggested, but can still be typed.
- **Interrupted runs:** the startup check is the only recovery. If a database failure stops a run from saving its final status, it stays `running` until the next backend start, and the dashboard stops waiting after 10 minutes.
- **No frontend tests:** these are verified by lint, build and a browser run. The later pipeline milestone needs frontend tests.
- **Not secured:** there is no login, and the backend still uses kind's default admin kubeconfig. This is for local use only until Milestone 7.
- **Known test issue:** the `liveness-demo` probe test from [Milestone 4](M4_LLM_INTEGRATION.md#final-verification-results) remains timing-sensitive. It passed in this run, which does not mean it is fixed.

## Run it

Start the backend and frontend as described in the [README](../README.md), then open <http://localhost:5173>. `sample-app` is healthy. To see real failures, use the [lab](M4_LLM_INTEGRATION.md#verify-with-the-real-provider).

```powershell
# From backend/
.\.venv\Scripts\python.exe -m pytest tests\test_investigations_api.py
.\.venv\Scripts\python.exe -m pytest -m cluster tests\test_investigations_live.py
```

Tests use a temporary database, never the real one.

## Final verification results

Recorded on 2026-11-02, on a local kind cluster, with no real provider calls.

| Check | Result |
|---|---|
| Non-cluster suite | 840 passed, 46 deselected |
| Real-cluster suite, run once | 46 passed, 840 deselected |
| `npm run lint` | No problems |
| `npm run build` | Succeeded |

**Browser run:** real headless Chrome, driven by a script, against the real backend and kind cluster, through the Vite proxy.

| Scenario | Result |
|---|---|
| Real failures in the lab namespace | 13 diagnoses and 94 evidence records shown, matching the saved record |
| Live progress | 6 stages and an `end` event arrived over SSE, across about 0.9 seconds |
| Invalid namespace | Rejected with a message, and no record saved |
| Missing namespace | Shown as failed with `namespace_not_found` |
| AI requested with the LLM disabled | Diagnosis shown, with the AI status reported as unavailable |
| Backend killed while following a run, then restarted | "Status is unknown" shown while down, then failed with `interrupted` |
| Backend restarted, page reloaded | All 5 saved investigations listed, and a saved report reopened with its evidence and progress |
| Phone width | No sideways scrolling |

The interruption scenario used one seeded `running` record, because a real investigation finished too quickly, in about a second, to kill the backend during it. The restart handling and the dashboard's response were real.
