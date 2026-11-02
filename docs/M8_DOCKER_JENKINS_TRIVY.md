# Milestone 8: Docker, Jenkins CI/CD and Trivy

**Goal:** build real Docker images and run a real Jenkins pipeline that tests, builds and scans the project, locally and at no cost.

**Acceptance criterion:** Jenkins checks out the repository, runs tests and builds, validates Kubernetes manifests, runs Trivy scans, publishes genuine results and fails correctly when required stages fail.

**Status:** M8 completed.

## Overview

```text
APP: docker-compose.yml, project troubleshooting-agent
  127.0.0.1:8088 -> frontend (nginx) -> backend:8000 -> kind network -> API server:6443
                                        history in a named volume
                                        restricted kubeconfig, mounted read-only

CI: ci/docker-compose.yml, project troubleshooting-ci
  127.0.0.1:8090 -> Jenkins -> TLS -> ci-docker (its own Docker daemon)
                     |                 runs tests, builds and Trivy
                     +-- the repository's .git, read-only
                     no kubeconfig, no host Docker socket, not on the kind network
```

The two projects are separate, with their own names, networks, volumes and ports. The development servers on ports 8000 and 5173 are unaffected. Nothing is pushed to any registry.

| File | Job |
|---|---|
| [backend/Dockerfile](../backend/Dockerfile) | Backend image: non-root, no `pip`, no tests |
| [frontend/Dockerfile](../frontend/Dockerfile), [nginx.conf](../frontend/nginx.conf) | Production build, served by unprivileged nginx |
| [docker-compose.yml](../docker-compose.yml) | Runs the application |
| [Jenkinsfile](../Jenkinsfile) | The pipeline |
| [ci/docker-compose.yml](../ci/docker-compose.yml) | Jenkins and its Docker daemon |
| [ci/jenkins/](../ci/jenkins/) | Jenkins image, pinned plugins and configuration as code |
| [ci/smoke-test.sh](../ci/smoke-test.sh), [ci/trivy-scan.sh](../ci/trivy-scan.sh) | Pipeline checks |

## Resource limits

| Container | Memory limit | CPU limit | Highest seen |
|---|---|---|---|
| App backend | 384 MiB | 1.0 | About 80 MiB at rest |
| App frontend | 64 MiB | 0.25 | About 5 MiB at rest |
| Jenkins | 1.2 GiB (Java heap 512 MiB) | 1.0 | About 540 MiB |
| CI Docker daemon, with everything it runs | 3 GiB | 2.0 | About 1.4 GiB |

Docker Desktop has 7.7 GiB on this machine, and kind uses about 1.1 GiB. Run the application or CI, not both, and keep other projects' stacks stopped during builds. Nothing restarts by itself. Building and verifying this milestone used about 10 GB of disk in total, for images, build cache and CI data.

## Run the application

Run these from the repository root, once the [Milestone 7 RBAC manifest](M7_RBAC_SECURITY.md#set-up) has been applied:

```powershell
backend\.venv\Scripts\python.exe scripts\new_readonly_kubeconfig.py --container
docker compose up -d --build
```

Open <http://localhost:8088>.

```powershell
# Stop, keeping history
docker compose down
```

**Why a separate kubeconfig:** the normal restricted file points at `127.0.0.1`, which means something else inside a container. The container's copy uses `troubleshooting-lab-control-plane:6443`, which resolves on the `kind` network and is covered by the API server's certificate. `host.docker.internal` is not covered.

**Renewing the token:** the token lasts 8 hours. Run the `--container` command again, with no restart. Only the dedicated folder `%USERPROFILE%\.kube\troubleshooting-agent-container` is mounted, because a replaced file would not be seen through a single-file mount, and `~/.kube` itself holds administrator credentials. The script refuses to use that folder.

History here is separate from the development backend's, in the volume `troubleshooting-agent_agent-data`.

## Set up and run Jenkins

**1. Create the admin password, once.** It stays outside the repository and is passed as a Compose secret.

```powershell
$dir = "$env:USERPROFILE\.troubleshooting-ci"
New-Item -ItemType Directory -Force $dir | Out-Null
$bytes = New-Object byte[] 24
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
[IO.File]::WriteAllText("$dir\admin-password", (($bytes | ForEach-Object { $_.ToString('x2') }) -join ''))
```

**2. Start it:**

```powershell
docker compose -f ci/docker-compose.yml up -d --build
```

**3. Sign in** at <http://localhost:8090> as `admin`, with the contents of that file. Configuration as code has already created the user and the `troubleshooting-agent` job, with no setup wizard.

**4. Run it:** open the job, then **Build with Parameters**, then **Build**. `BRANCH` defaults to `main`. The job needs the `Jenkinsfile` to be committed on the branch it builds, because Jenkins sees only committed files.

```powershell
# Stop, keeping Jenkins data, build history and caches
docker compose -f ci/docker-compose.yml down
```

**How it connects:**

| Connection | How |
|---|---|
| Git | There is no remote, so the job clones `file:///repo.git`, a read-only mount of only `.git`. Jenkins blocks local checkouts by default, so `ALLOW_LOCAL_CHECKOUT` is set explicitly. Without the rest of the repository, it cannot see `.venv`, `.env` or the database |
| Docker | `DOCKER_HOST=tcp://docker:2376` with TLS, to the `ci-docker` container |
| Kubernetes | None, on purpose |
| Credentials | Jenkins stores none. The pipeline uses none |

**Tools:** Jenkins 2.580.2, Docker 29, Trivy 0.76.1, kubeconform, Python 3.13, Node 22. Images are pinned by digest, and all 72 Jenkins plugins, including their dependencies, are pinned in [plugins.txt](../ci/jenkins/plugins.txt). The job builds on one executor. To see the images it builds:

```powershell
docker exec ci-docker docker images --filter label=project=troubleshooting-agent
```

## Why Jenkins has its own Docker daemon

| | Own daemon (used here) | Host Docker socket |
|---|---|---|
| Can a pipeline mistake affect other projects' containers? | No, it cannot see them | Yes, for example a stray prune |
| Needs | A `privileged` container | Effective root on the Docker host |
| Memory | Capped as one unit | Builds are unbounded |
| Disk | Base images are stored twice | Shared |

**This protects against mistakes. It is not a security boundary.** A privileged container could escape if it ran malicious code, which is why this builds only this repository and listens only on this machine.

## The pipeline

Stages run in order, and a failure skips everything after it. A required command is never allowed to fail quietly.

| Stage | What it does | Fails when |
|---|---|---|
| Checkout | Clones the branch, from a clean workspace | The clone fails |
| Backend dependencies | Installs `requirements-dev.txt` in a virtual environment, with a pip cache | The install fails |
| Backend lint | `ruff check` on the application, tests and scripts | Ruff finds a problem |
| Backend tests | `pytest` with a JUnit report, as a non-root user, with no cluster or credentials | Any test fails |
| Frontend lint | `npm ci`, then lint with warnings treated as failures | Any finding |
| Frontend build | The production build | The build fails |
| Validate manifests | kubeconform in strict mode, against Kubernetes 1.37.0, on `k8s/base`, `k8s/rbac` and `k8s/scenarios` | Any invalid manifest |
| Build images | Both images, tagged with the commit | A build fails |
| Smoke test | Runs both images together. Checks non-root users, health, the nginx proxy, and that no credentials means a clear 503 | Any check fails |
| Trivy | See below | The gate fails, or a scan cannot run |

After every build, Jenkins publishes the JUnit report and archives everything in `reports/`. If a build fails, its images are removed so they cannot look usable. Cleanup only ever targets this project's label on the CI daemon, and never prunes globally.

`kind-config.yaml` is left out of validation because it configures kind and is not a Kubernetes object.

**Cluster tests are not in the pipeline.** Jenkins has no cluster access, by design. Run them by hand from the host, as described in [Milestone 7](M7_RBAC_SECURITY.md#run-the-tests).

## The Trivy policy

| Scan | Scope | Result |
|---|---|---|
| Full report | Both images, every severity, in JSON and as a table | Archived. Never decides the result |
| **Image gate** | Both images: `HIGH` and `CRITICAL` with a fix available, and any secret | **Fails the build** |
| **Dependency gate** | `requirements*.txt`, `package-lock.json` and committed source, the same way | **Fails the build** |
| Misconfiguration | Dockerfiles and manifests | Archived, for reading only |

**How this avoids false passes:**

- A scan that cannot run fails the build. If the vulnerability database cannot be downloaded, it retries once and then fails.
- Every scan uses that fresh database, so old results are never reused.
- Findings with no available fix do not block the build, because nothing can be done about them yet. They stay in the full report.
- `.trivyignore.yaml` does not exist. An exception should be rare, for one specific finding, with a reason and an expiry date, and reviewed by a person.
- The gate leaves out `backend/tests`, which holds deliberately fake credentials for testing redaction. The images never contain those files.

**Fixing a finding:** update the base image or the pinned dependency. Never lower the severity to get a green build.

## Keeping credentials out

| Credential | Where it goes |
|---|---|
| Administrator kubeconfig | Nowhere near Docker: not in an image, build context, Compose file, Jenkins or the logs |
| Restricted kubeconfig | Only the application backend, from its dedicated folder, read-only |
| Jenkins admin password | A Compose secret from outside the repository |
| OpenRouter key | Not passed to anything. The LLM stays disabled in containers |

- **Build contexts:** `.dockerignore` in each image folder allows only named files, so anything new stays out by default.
- **Non-root:** the backend runs as user 10001 and can write only to its database folder. The frontend runs as user 101.
- **Never set in Compose:** `KUBERNETES_ALLOW_DEFAULT_KUBECONFIG`.

## Limitations

- **Unfixed vulnerabilities remain in the backend image:** Debian's packages in `python:3.13-slim` have known findings with no fix available yet. In the final build: 45 HIGH, 71 MEDIUM, 61 LOW and 8 UNKNOWN. These are reported and do not block the build, and they are not claimed to be harmless. A smaller base image could reduce them.
- **Findings change over time:** a new CVE can fail a build with no code change. That is correct behaviour.
- **Builds are not fully reproducible:** the frontend runs `apk upgrade` to pick up security fixes newer than its pinned base image.
- **Cached layers can hold older packages:** a cached image layer does not pick up new fixes until it is rebuilt. Trivy still scans the result, so an affected image fails the gate.
- **Internet needed:** first builds pull images, and every build updates Trivy's database and downloads manifest schemas. If these fail, the build fails.
- **Privileged container:** see above.
- **No frontend tests:** they were deferred, so the frontend is checked by lint, build and the smoke test. The master requirements list them, and they remain to be added.
- **Formatting not checked:** Ruff checks lint rules only, to avoid a large reformatting change.
- **Manual builds:** with no remote there is nothing to trigger a build, so you start them.
- **Cluster tests are manual**, as above.
- **Windows and Docker Desktop:** verified on this setup only. Mount permissions and paths such as `USERPROFILE` are specific to it.

## Troubleshooting

| Symptom | Likely reason | What to do |
|---|---|---|
| Compose reports that `USERPROFILE` or a secret file is missing | Not PowerShell, or the password file was not created | Run from PowerShell, and do step 1 of the Jenkins set-up |
| The dashboard reports `kubeconfig_unavailable` or rejected credentials | No container kubeconfig, or the token expired | Run the `--container` command again |
| `network kind declared as external, but could not be found` | The kind cluster does not exist | See [Milestone 1](M1_LOCAL_KUBERNETES.md) |
| Build fails with `Couldn't find any revision to build` | That branch does not exist, or the `Jenkinsfile` is not committed on it | Commit it, and check `BRANCH` |
| Trivy fails at the database download | No internet, or a registry rate limit | Check the connection, then build again. Do not skip the scan |
| Trivy's gate fails with no code change | A new vulnerability with a fix was published | Update the base image or the dependency |
| A timing test fails only in Jenkins | CPU limits on `ci-docker` | Stop other work and rerun. Report it, and do not weaken the test |
| Jenkins cannot reach Docker | `ci-docker` is not healthy | `docker compose -f ci/docker-compose.yml ps`, and check its logs |
| The machine is slow during builds | Too much running at once | Stop the application stack and other projects |

## Roll back

These commands affect only this milestone's two Compose projects:

```powershell
docker compose down -v --rmi local
docker compose -f ci/docker-compose.yml down -v --rmi local
```

This deletes the Compose application's history, Jenkins data, build history and CI caches. Base images pulled to the host remain. Never use `docker system prune` here, because other projects' stopped containers and volumes would be at risk.

## Interview walkthrough

1. **Open Jenkins** at <http://localhost:8090> and show the stage view of a green build.
2. **Open the test result** for the real test count, which runs on Linux in a container.
3. **Open the archived Trivy reports.** Explain the gate, and be direct about the unfixed findings: "they are visible, nothing can fix them yet, and a fix becoming available would fail the build."
4. **Show the images:** `docker exec ci-docker docker images --filter label=project=troubleshooting-agent`.
5. **Show a failed build.** History holds real examples, such as a planted fake token that the secret scan caught.
6. **Stop CI, start the application,** and run the [Milestone 6 demonstration](M6_FAILURE_SCENARIOS.md#interview-walkthrough) at <http://localhost:8088>.

**Questions you may be asked:**

- **"Why Docker-in-Docker, and not the socket?"** A pipeline mistake cannot touch other projects' containers here. It needs a privileged container, so it protects against accidents and is not a boundary against malicious code.
- **"Why is `pip` removed from the backend image?"** The application never uses it, and it carries its own older copies of libraries such as `urllib3`. Scanners were right to report those, so removing it is a real fix.
- **"Why does Jenkins have no cluster access?"** The pipeline does not need it. The smoke test proves the image refuses to work without credentials, and cluster tests run separately.
- **"How do you know the gates work?"** Each was tested with a deliberate fault. See below.
- **"What would you change for production?"** A real registry with image signing, separate agents, webhooks from a Git host, and no privileged containers.

## Final verification results

Recorded on 2026-11-02, on Docker Desktop with WSL2.

| Check | Result |
|---|---|
| Non-cluster suite on Windows, with no credentials set | 960 passed, 126 deselected |
| Real-cluster suite, with the backend restricted | 126 passed, 960 deselected |
| Non-cluster suite on Linux, in Jenkins | 960 passed, 126 deselected |
| Ruff, frontend lint and build on Windows | No problems |
| Full pipeline | Build 13: `SUCCESS`, all 10 stages, on a temporary branch holding the finished code, before this table was filled in |
| Jenkins restart | Job and all 12 earlier builds kept |
| Full Milestone 6 demonstration in Chrome, through the Compose application | Not Verified, Not Verified, Still Failing, Recovered, with no page errors |
| Real token inside the backend container | Delete deployment 403, delete namespace 403, Secrets 403, list pods allowed |
| Token renewal | Seen by the running container with no restart |
| History across `down` and `up` | Kept |
| Search for 5 real credentials in every image layer, image history, all 13 Jenkins consoles, the Jenkins home, and the files Git would commit | None found |
| Other projects' containers, volumes and networks, compared before and after | No changes |

**The Trivy gate caught real issues on the first run, which were fixed, not excepted:**

| Image | Found | Fix |
|---|---|---|
| Backend | 5 HIGH, in copies of libraries inside `pip` | `pip` removed from the image |
| Frontend | 1 CRITICAL and 46 HIGH, in an outdated base image | Updated to the current stable image, plus `apk upgrade` |

**Each gate was tested with a deliberate fault,** on temporary branches that were then deleted:

| Fault | Failed at | Evidence |
|---|---|---|
| A failing test | Backend tests | The JUnit report showed 1 failed, 960 passed |
| An unused import | Backend lint | Ruff F401 |
| A `debugger` statement | Frontend lint | `no-debugger` |
| An unresolved import | Frontend build | `UNRESOLVED_IMPORT` |
| An invalid manifest | Validate manifests | Schema validation failed |
| A backend that cannot start | Smoke test | Did not become healthy |
| An outdated base image | Trivy | `GATE FAILED` for the frontend |
| A fake GitHub token in the image | Trivy | Reported as CRITICAL, and masked in the log |
| An unreachable vulnerability database | Trivy | Retried once, then failed |

In every case, the later stages were skipped and the build result was `FAILURE`.
