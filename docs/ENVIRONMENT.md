# Development Environment

Recorded during Milestone 0 on 2026-11-01. These are measurements from one machine, so re-check them if the machine or Docker Desktop changes.

## Machine

| Item | Value |
|---|---|
| OS | Windows 10 Pro, build 19045 |
| CPU | Intel i5-6300U: 2 cores, 4 threads |
| RAM | 15.9 GB total, 6.7 GB free when measured |
| Disk C: | 61.3 GB free of 237.8 GB |

## Tools

| Tool | Version | Notes |
|---|---|---|
| Python | 3.13.3 | |
| Node.js / npm | 22.15.0 / 11.3.0 | Meets the `create-vite` requirement of 22.12 or newer |
| Git | 2.54.0 | |
| Docker | 29.7.2 | Docker Desktop, WSL2 engine, 4 CPUs and 7.7 GB available to containers |
| kubectl | 1.36.1 | Bundled with Docker Desktop |
| kind | Not installed | Planned for Milestone 1 |
| Trivy | Not installed | Planned for Milestone 8 |
| Jenkins | 2.555.3 | Already running as a Windows service on port 8080, set to start automatically |

## Constraint: Docker uses cgroup v1

`docker info` reports `CgroupVersion=1` on kernel `5.15.167.4-microsoft-standard-WSL2`.

Cgroups are the Linux kernel feature that limits a container's CPU and memory. Since Kubernetes 1.35 the kubelet's `failCgroupV1` setting defaults to `true`, so the kubelet does not start on a cgroup v1 host. See the [Kubernetes cgroup v2 article](https://kubernetes.io/blog/2026/10/06/kubernetes-cgroups-v2-shift/).

kind v0.33.0 defaults to Kubernetes 1.37, so a plain `kind create cluster` is expected to fail on this machine. This comes from the documentation and has not been tested here yet.

**Milestone 1 plan:** use the newest image built for kind v0.33.0 that predates the change, pinned by digest as the release notes require:

```text
kindest/node:v1.34.11@sha256:44e222ee2132dab25ff87301682f89eb82c7880ea3a1bf543bfe9708fd08d67d
```

This needs no changes to the Windows or WSL configuration, so other projects are not affected. Two follow-ups for Milestone 1:

- The bundled kubectl 1.36 is two minor versions ahead of a 1.34 cluster, while Kubernetes supports a difference of one. Check whether a matching kubectl is needed.
- Check whether 1.34 is still within upstream support, and record the answer as a known limitation if it is not.

## Other observations

- **Virtualization:** `VirtualizationFirmwareEnabled` reads `False`, but `HypervisorPresent` is `True` and Linux containers run. Privileged containers, which kind needs, also run.
- **Docker counters:** `docker info` reported 6 running containers while `docker ps` listed none and `docker system df` showed 0 active. `docker ps` was treated as the accurate source.
- **WSL listing:** `wsl -l -v` shows `docker-desktop` as `Stopped` even though containers run on the WSL2 kernel. The cause is unknown and it has not blocked anything.

## Ports

| Port | Current or expected use |
|---|---|
| 8000 | This project's backend |
| 5173 | This project's frontend dev server |
| 8080 | Existing Jenkins Windows service |
| 8081 | Terraform project nginx (container currently stopped) |
| 3000 | Terraform project Grafana (container currently stopped) |
| 9090 | Terraform project Prometheus (container currently stopped) |

Milestones 8 and 9 must avoid these ports or run while those services are stopped.

## Change from the planned stack

The requirements list `httpx` for backend tests. This project uses `httpx2` because Starlette's `TestClient` now deprecates `httpx` and documents `httpx2` as its dependency.
