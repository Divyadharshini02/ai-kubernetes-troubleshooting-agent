# Milestone 9: Prometheus and Grafana Monitoring

**Goal:** expose real operational metrics from the backend, and show them in Grafana.

**Acceptance criterion:** dashboards reflect real investigation activity.

**Status:** completed. See [final verification results](#final-verification-results), and the [remaining security findings](#remaining-security-findings).

## The design in short

```text
Browser -> 127.0.0.1:8088 -> nginx -> /api/* -> backend:8000 -> kind (read-only identity)
                                                    |
                                       /metrics (not proxied, not published)
                                                    | scraped every 15 s over the app network
                     127.0.0.1:9091 -> Prometheus (7 days of data, 3 alert rules)
                                                    | PromQL
                     127.0.0.1:3001 -> Grafana (provisioned data source and 1 dashboard)
```

| Part | File |
|---|---|
| Metric definitions and recording | [app/observability/metrics.py](../backend/app/observability/metrics.py) |
| HTTP metrics | [app/observability/middleware.py](../backend/app/observability/middleware.py) |
| Scrape configuration | [monitoring/prometheus/prometheus.yml](../monitoring/prometheus/prometheus.yml) |
| Alert rules and their tests | [alerts.yml](../monitoring/prometheus/alerts.yml), [alerts.test.yml](../monitoring/prometheus/alerts.test.yml) |
| Grafana provisioning | [monitoring/grafana/provisioning](../monitoring/grafana/provisioning) |
| The dashboard | [troubleshooting-agent.json](../monitoring/grafana/dashboards/troubleshooting-agent.json) |

Every panel is a live PromQL query. Nothing is hardcoded, and a test checks that.

## Run it

Prometheus and Grafana are in the root `docker-compose.yml` under the `monitoring` profile. A plain `docker compose up` still starts only the application.

Create the Grafana password once, from the repository root. It is stored outside the repository:

```powershell
$dir = "$env:USERPROFILE\.troubleshooting-monitoring"
New-Item -ItemType Directory -Force $dir | Out-Null
$bytes = New-Object byte[] 24
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
[IO.File]::WriteAllText("$dir\grafana-admin-password", (($bytes | ForEach-Object { $_.ToString('x2') }) -join ''))
```

Set up the application as described in [Milestone 8](M8_DOCKER_JENKINS_TRIVY.md#run-the-application), stop the CI stack if it is running, and start everything:

```powershell
docker compose --profile monitoring up -d --build --wait
```

| URL | Purpose | Login |
|---|---|---|
| <http://localhost:8088> | The application | None |
| <http://localhost:3001/d/troubleshooting-agent> | The dashboard | `admin`, with the password from the file above |
| <http://localhost:9091/targets> | Scrape status | None, loopback only |
| <http://localhost:9091/alerts> | Alert states | None, loopback only |

The panels stay quiet until something happens. Run an investigation at <http://localhost:8088>, and within about 30 seconds the dashboard shows it.

To stop everything and keep all data:

```powershell
docker compose --profile monitoring down
```

**Always include the profile.** A plain `docker compose down` leaves Prometheus and Grafana running, and cannot remove the shared network. **Do not add `-v`:** it would also delete `agent-data`, your investigation history.

To remove only monitoring and its data:

```powershell
docker compose --profile monitoring rm -sf prometheus grafana
docker volume rm troubleshooting-agent_prometheus-data troubleshooting-agent_grafana-data
```

## Metrics

All custom metrics start with `troubleshooting_`.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `http_requests_total` | Counter | `method`, `route`, `status` | Finished requests, except scrapes of `/metrics` |
| `http_server_errors_total` | Counter | None | Finished requests with a 5xx status |
| `http_request_duration_seconds` | Histogram | `method`, `route` | Time until the last response byte, without the event stream |
| `investigations_total` | Counter | `status`, `outcome` | Investigations whose final result was saved. `outcome` is `none` for failures |
| `investigation_failures_total` | Counter | `code` | Saved failures, by failure code |
| `investigation_duration_seconds` | Histogram | `status` | How long an investigation ran |
| `diagnoses_total` | Counter | `scenario`, `status` | Diagnoses in saved reports |
| `recovery_verifications_total` | Counter | `status` | `recovered`, `still_failing` or `not_verified` |
| `evidence_collector_failures_total` | Counter | `category`, `status` | Evidence records with `forbidden`, `failed` or `skipped` status in saved reports |
| `llm_request_duration_seconds` | Histogram | None | One provider attempt. Retries count separately |
| `llm_request_errors_total` | Counter | `reason` | Provider attempts that failed |
| `llm_explanations_total` | Counter | `status`, `reason` | Explanation requests. `unavailable` is the fallback to the deterministic result |

Prometheus adds `up`, and the client library adds `process_*` and `python_*`.

### What stops the numbers being wrong

- **Each result is counted once, after it is saved.** `run_investigation` records only after a final status has been written to SQLite. A report that cannot be saved is never counted as completed. If the failure can be saved instead, that counts as one failure. If neither can be saved, nothing is counted.
- **SQLite stays the source of truth.** Tests and the live check compare the metrics with what was saved.
- **A monitoring fault cannot change a result.** Recording catches its own errors, so a metric error cannot turn a saved report into a failure.
- **Each request is counted once.** A flag in the middleware covers normal responses, client disconnects, cancellation and application errors.
- **Timing stops at the last response byte.** FastAPI runs background tasks inside the same request call, so timing the whole call would make `POST /api/investigations` look as slow as the investigation.
- **Retries are not extra fallbacks.** Two failed attempts on one request are two `llm_request_errors` and one `llm_explanations{status="unavailable"}`.

### Why labels are limited

Each distinct label value creates a new series, which costs memory forever. So namespaces, pod names, investigation IDs, users, secrets and log text are never labels. Every value comes from a fixed list, and anything unexpected becomes `other`, `OTHER` or `unmatched`. Routes use the template, such as `/api/investigations/{investigation_id}`, never the requested path. A test requests 1,000 random paths and checks that no series appear.

### Why series start at 0

Prometheus needs two samples to see an increase. A counter whose first sample is already 6 looks like no change at all. So every known label combination is created at 0 when the backend starts.

HTTP series cannot do this, because their routes and statuses are only known once used. This caused a real bug during verification: `BackendErrorsHigh` used those series and did not fire after six real 502 responses. Its rule test passed, because the simulated series started at 0. That is why `http_server_errors_total` exists, with no labels and starting at 0.

## The dashboard

One dashboard, **Troubleshooting Agent**, with 12 panels. Metric names here leave out the prefix. [Milestone 10](M10_AI_AGENT.md#monitoring) added two more, for AI analyses and chat, and left these unchanged.

| Panel | PromQL |
|---|---|
| Backend up | `up{job="backend"}` |
| Availability | `avg_over_time(up{job="backend"}[$__range])` |
| Investigations | `round(sum(increase(investigations_total[$__range])))` |
| Failure ratio | `sum(rate(investigations_total{status="failed"}[$__range])) / sum(rate(investigations_total[$__range]))` |
| Outcomes over time | `sum by (status, outcome) (increase(investigations_total[$__rate_interval]))` |
| Investigation duration | `histogram_quantile(0.95, sum by (le) (rate(investigation_duration_seconds_bucket[5m])))`, and the same for 0.50 |
| Failures by code | `round(sum by (code) (increase(investigation_failures_total[$__range]))) > 0` |
| Diagnoses by scenario | `round(sum by (scenario) (increase(diagnoses_total[$__range]))) > 0` |
| Recovery results | `round(sum by (status) (increase(recovery_verifications_total[$__range]))) > 0` |
| Requests and 5xx rate | `sum by (route) (rate(http_requests_total{route!="/api/health"}[$__rate_interval]))`, and `rate(http_server_errors_total[$__rate_interval])` |
| HTTP p95 by route | `histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket{route!="/api/health"}[5m])))` |
| Collector failures and LLM status | The same `increase` form over `evidence_collector_failures_total` and `llm_explanations_total` |

Traffic panels leave out `/api/health`, because Docker calls it every 15 seconds. The dashboard and data source are loaded from files, so they return after a restart or even after deleting Grafana's volume. Changes made in the Grafana page are not allowed: change the files instead.

**Reading the numbers:** `increase()` estimates between scrapes, so the bars in "Outcomes over time" can be a little higher than the true count. Counts in the stat panels are rounded. For an exact count, use the investigation history.

**`None` and `No data` are normal.** They are expected when no matching activity exists in the selected time range. For example, "Failures by code" shows `None` when nothing failed, and "HTTP p95 by route" shows `No data` when there was no traffic to measure. "Failure ratio" and "Investigation duration" stay blank, because there is nothing to calculate. They are not dashboard errors. A query error would show as an error on the panel.

## Alerts

| Alert | Condition | Waits | What it means |
|---|---|---|---|
| `BackendDown` | `up{job="backend"} == 0` | 1 minute | Prometheus cannot reach the backend |
| `BackendErrorsHigh` | `increase(http_server_errors_total[5m]) >= 5` | No | Many 5xx responses, including credential and cluster problems |
| `ClusterAccessFailing` | `sum(increase(investigation_failures_total{code=~"cluster_access_denied\|kubeconfig_unavailable\|cluster_unreachable"}[15m])) > 0` | No | Investigations fail because the cluster cannot be used, often an expired 8-hour token |

A mistyped namespace does not raise `ClusterAccessFailing`, because that is user input and not an incident.

Alerts appear in Prometheus only. There is no Alertmanager, so nothing sends a notification, on purpose: nobody is on call for a local project. A real system would add one for routing and silencing.

The last two clear only when their time window has passed, so up to 5 and 15 minutes after the fix.

## Security

- **`/metrics` has no login,** like the rest of the API. In Docker it is not published, and nginx does not proxy it. A local development backend exposes it on loopback only, on the same port as the API.
- **Metrics hold counts and fixed labels only.** Tests look for tokens, names and IDs in the output.
- **Grafana:** the password comes from a Compose secret file outside the repository, and it is not in `docker inspect`. Anonymous access and sign-up are off, and telemetry and update checks are disabled.
- **Prometheus has no login,** so it is published on loopback only. Its admin and lifecycle APIs are off.
- **No cluster access:** neither service joins the `kind` network, or gets a kubeconfig or the Docker socket. Both drop all Linux capabilities, and have memory and CPU limits.
- **Separate from other projects:** the volumes belong to this Compose project, and the ports avoid those claimed by other projects on this machine.

### Remaining security findings

These are known and accepted for a local project. They are not risk-free.

- **Backend image vulnerabilities:** build #17 reports 45 HIGH, 71 MEDIUM, 61 LOW and 8 UNKNOWN findings. All are in Debian packages, and none are in Python dependencies. Trivy lists no fixed version for any of them: 183 are `affected` and 2 are `fix_deferred`. M9 did not add any: the counts match Milestone 8. No fix available does not mean they cannot be exploited, and that has not been assessed. The HIGH findings are in the `util-linux` family, `login`, `mount`, `perl-base`, `libsqlite3-0`, `libsystemd0`, `libudev1`, `libacl1` and `ncurses`. `libsqlite3-0` deserves the first look, because the application stores its history in SQLite. The gate does not block these today. A rebuild fails once a fix for a HIGH or CRITICAL finding exists and the image still has the problem.
- **Prometheus and Grafana images are not scanned,** so their vulnerabilities are unknown. Loopback-only access limits the exposure.
- **Prometheus has no login,** so any local process can read its metrics and configuration, which hold no credentials.
- **`/metrics` has no login,** and other containers on the `app` and `kind` networks may be able to reach it. Reachability from cluster pods was not tested. The API has had the same exposure since Milestone 8.
- **Grafana's `secret_key` is the default.** It stores no data source passwords, so this has little effect here. A real deployment must set its own.
- **No TLS:** Prometheus and Grafana use plain HTTP on loopback.
- **Backend and frontend containers keep their Milestone 8 settings,** and do not drop Linux capabilities as the monitoring containers do.

## Jenkins

| Check | Stage | Fails when |
|---|---|---|
| `promtool check config`, `check rules`, `test rules` | **Validate monitoring** (new) | Configuration or rules are invalid, or an alert behaves differently from its test |
| Metric, config and dashboard tests | Backend tests | A dashboard or alert uses an unknown metric, a label is forbidden, a port is not on loopback |
| `/metrics` works, counts real requests, not served by nginx | Smoke test | The endpoint is missing or exposed through the frontend |
| Trivy scans the backend image with the new dependency | Trivy | The existing gates, unchanged |

Prometheus and Grafana do not run in Jenkins, to save memory. Trivy does not scan their images, because they are not built here, so their fixable vulnerabilities would be outside this project's control.

## Resources

Measured on this machine with Docker Desktop, 7.7 GiB and 4 CPUs, with the CI stack stopped.

| Component | Measured memory | Limit | CPU limit |
|---|---|---|---|
| Grafana | 176 to 235 MiB | 384 MiB | 0.5 |
| Prometheus | 49 to 53 MiB | 256 MiB | 0.5 |
| Backend | 78 to 86 MiB | 384 MiB | 1.0 |
| Frontend | 5 MiB | 64 MiB | 0.25 |
| kind control plane | 1.1 GiB | None | None |

**Memory is not disk.** `grafana/grafana-oss:13.0.2` takes 1.47 GB of disk, and Prometheus 381 MB, as `docker images` reports them. They were already on this machine, so reusing them took no additional disk. The monitoring volumes hold a few megabytes after these tests, and Prometheus is capped at 7 days or 256 MB. If the images are missing on another machine, `up` downloads them.

Do not run the application and CI stacks together.

## Limitations

- **Counters reset when the backend restarts.** `rate()` and `increase()` handle resets, but the totals are not lifetime totals. The history in SQLite is.
- **Errors in the first 15 seconds after a start may be missed,** before the first scrape records the 0.
- **New HTTP series miss their first increase,** so request rates per route are slightly low when a route or status is first used. Alerts do not depend on these.
- **Interrupted investigations are not counted,** because the process that ran them is gone. They are marked at the next start, and stay in SQLite.
- **Results that could not be saved at all are not counted.** They appear only in the backend log.
- **Metrics cover investigations.** The `/diagnosis` endpoint adds HTTP and LLM metrics, not investigation metrics.
- **LLM metrics are verified against a local stand-in provider only,** since no real provider calls were made. See [Milestone 4](M4_LLM_INTEGRATION.md).
- **Prometheus and Grafana images are not scanned,** and their ports have no TLS. Loopback access is the protection.
- **One backend process.** Several worker processes would each have separate counters, and would need a different setup.
- **A local demonstration, not a production design:** one Prometheus, no high availability, no long-term storage.

## Interview walkthrough

Run this from the repository root, with the stack started as above.

1. **Show nothing is exposed:**
   ```powershell
   (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8088/metrics).Content -match 'troubleshooting_'   # False
   ```
2. **Show the real scrape,** at <http://localhost:9091/targets>: `backend` is `UP`.
3. **Investigate `sample-app`** at <http://localhost:8088>, then a namespace that does not exist. Open the dashboard: Investigations and Failures by code change.
4. **Prove it matches.** Compare with the history in the application.
5. **Run the [Milestone 6 demonstration](M6_FAILURE_SCENARIOS.md#interview-walkthrough)** and watch Diagnoses by scenario and Recovery results.
6. **Fire an alert.** Stop the backend, and watch <http://localhost:9091/alerts> go to pending, then firing after about a minute. Start it, and it clears:
   ```powershell
   docker compose stop backend
   docker compose start backend
   ```

**Questions you may be asked:**

- **"Why pull and not push?"** Prometheus decides when to scrape, so `up` itself reports when a target is down. A service that has died cannot push that news.
- **"Why not put the namespace in a label?"** Users supply it, so series could grow without limit. That is the classic cardinality mistake.
- **"How do you know the numbers are right?"** Results are recorded after saving, and I compared the counters with SQLite after real investigations.
- **"Tell me about a bug monitoring found."** My error alert did not fire on real 502s, though its unit test passed. A series first seen at 6 looks like no increase. I added a counter that starts at 0, and proved the alert live. Rule tests only prove the rule against the data you imagine.
- **"Why a histogram and not an average?"** An average hides slow outliers. A histogram gives percentiles, at the cost of a few fixed buckets, and it is an estimate.
- **"Why provision dashboards from files?"** They are reviewed in Git, and return after the data is lost. A dashboard clicked together in the page is not reproducible.
- **"What would you add for production?"** Alertmanager with routing, TLS and login on Prometheus, recording rules, long-term storage, and SLOs with burn-rate alerts.

## Run the tests

From `backend/`:

```powershell
# No cluster needed
.\.venv\Scripts\python.exe -m pytest tests\test_metrics.py tests\test_metrics_middleware.py tests\test_metrics_investigations.py tests\test_monitoring_config.py

# Real cluster, read only
$env:TEST_ADMIN_KUBECONFIG = "$env:USERPROFILE\.kube\config"
$env:KUBECONFIG = "$env:USERPROFILE\.kube\troubleshooting-agent.kubeconfig"
.\.venv\Scripts\python.exe -m pytest tests\test_metrics_live.py -m cluster
```

To check the Prometheus files from the repository root, in PowerShell:

```powershell
$image = "prom/prometheus:v3.15.1@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2"
$files = "$((Get-Location).Path)\monitoring\prometheus:/etc/prometheus:ro"
docker run --rm -v $files -w /etc/prometheus --entrypoint promtool $image check rules alerts.yml
docker run --rm -v $files -w /etc/prometheus --entrypoint promtool $image test rules alerts.test.yml
```

## Final verification results

Recorded on 2026-11-02, on the local kind cluster, with the backend restricted and no real provider calls.

| Check | Result |
|---|---|
| Non-cluster suite | 1,043 passed: the earlier 960, and 83 new |
| Real-cluster suite | 128 passed: the earlier 126, and 2 new |
| Ruff, `npm run lint`, `npm run build` | No problems, and the frontend is unchanged |
| `promtool` config, rules and rule tests | All succeeded, with 3 rules found |

**Metrics against SQLite,** after real investigations in real Chrome, on the final image. These were: `sample-app`, a missing namespace, the Milestone 6 failure and recovery flow, an explanation request, and 3 real clients disconnecting from the event stream during a run.

| Compared | SQLite | Metrics |
|---|---|---|
| Completed, no problems | 7 | 7 |
| Completed, problems found | 2 | 2 |
| Failed, `namespace_not_found` | 1 | 1 |
| Recovery: recovered, still failing, not verified | 1, 1, 7 | 1, 1, 7 |
| Diagnoses: `image_pull_backoff`, `service_selector` | 2, 2 | 2, 2 |
| LLM: unavailable because disabled | 1 | 1 |

`/metrics`, Prometheus and Grafana all reported the same total of 10. All 9 event streams were counted once each, and there were 0 provider attempts. No namespace, pod name or investigation ID appeared in `/metrics`.

**Alerts,** each caused by a real fault:

| Alert | Cause | Fired | Cleared after the fix |
|---|---|---|---|
| `BackendDown` | Backend container stopped | Pending at 35 s, firing at 94 s | 41 s |
| `BackendErrorsHigh` | 6 real 502 responses from a rejected token | 29 s | 268 s |
| `ClusterAccessFailing` | Real investigation failed with `cluster_access_denied` | Within 2 minutes | 769 s |

The rejected token was a made-up one in a throwaway kubeconfig. The real restricted kubeconfig was not changed.

**Persistence:** the dashboard, 12 panels and data source returned after a restart, a recreate, and deleting Grafana's volume. Prometheus kept its samples across a restart.

**Grafana:** all 12 panels of the final dashboard were visually inspected in a browser, and rendered without visible errors. A separate Chrome capture after a real login showed the same, with no page errors. All 12 panels' queries also ran through Grafana with no query errors.

This shows that the panels work. It does not show that every panel held real data:

- That inspection was during a quiet period, so several panels correctly showed `None` or `No data`.
- During the real investigations above, the investigation, outcome, duration, failure, diagnosis, recovery, request, latency and LLM status queries returned real values.
- No evidence collector failed during those runs, so that query returned nothing live. Its counting is proven by tests.
- No LLM provider was called, so provider request metrics stayed at 0 live. They are proven with a local stand-in.

**Jenkins:** build #17 succeeded on all stages, with 1,043 tests.

| Trivy report | Fixable HIGH or CRITICAL | Secrets | Other findings |
|---|---|---|---|
| Backend image | 0 | 0 | 45 HIGH, 71 MEDIUM, 61 LOW and 8 unknown, none with a fix available |
| Frontend image | 0 | 0 | None |
| Dependency files and source | 0 | 0 | None. Nothing against `prometheus-client` |

Each deliberate fault failed the build at the expected stage:

| Fault | Failed at |
|---|---|
| Invalid alert rule | Validate monitoring |
| Rule changed so that its test no longer holds | Validate monitoring |
| Dashboard using an unknown metric | Backend tests |
| `/metrics` moved to another path | Backend tests |
| Smoke check not finding its metric | Smoke test |

These builds ran from a snapshot of the uncommitted work in a separate clone, so no commit was made in this repository. Only documentation changed after build #17. These results do not yet include a build of the committed `main`, which is the next step after this commit.

**Credentials:** the 6 real credentials were not found in the backend image and its metadata, the consoles of builds #17 to #23, or any file Git would commit. The Prometheus and Grafana volumes were not scanned.
