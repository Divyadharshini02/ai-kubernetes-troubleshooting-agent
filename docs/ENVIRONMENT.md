# Development Environment

Verified on 2026-11-01 on this project's development machine. These are measurements from one machine, so re-check them if the machine, WSL or Docker Desktop changes.

## Machine

| Item | Value |
|---|---|
| OS | Windows 10 Pro, build 19045 |
| CPU | Intel i5-6300U: 2 cores, 4 threads |
| RAM | 15.9 GB total |
| Disk C: | 237.8 GB total |

## Tools

| Tool | Version | Notes |
|---|---|---|
| Python | 3.13.3 | Project packages are installed only in `backend/.venv` |
| Node.js / npm | 22.15.0 / 11.3.0 | |
| Git | 2.54.0 | |
| Docker | 29.7.2 | Docker Desktop, WSL2 engine, 4 CPUs and 7.7 GiB available to containers |
| WSL | 2.4.13 | Kernel 5.15.167.4-1, cgroup v2 enabled by the setting below |
| kind | 0.33.0 | Installed with winget; SHA-256 matched the official checksum |
| kubectl | 1.36.1 | Bundled with Docker Desktop |
| Kubernetes | 1.37.0 | Single-node kind cluster |
| Kubernetes Python client | 37.0.1 | Pinned in `backend/requirements.txt` |
| Trivy | Not installed | Planned for Milestone 8 |
| Jenkins | 2.555.3 | Windows service on port 8080, set to start automatically |

kubectl 1.36 is one minor version behind the API server, which is [within the supported difference](https://kubernetes.io/releases/version-skew-policy/#kubectl).

## WSL setting: cgroup v2

Cgroups are the Linux kernel feature that limits a container's CPU and memory. Since Kubernetes 1.35 the kubelet [requires cgroup v2 by default](https://kubernetes.io/blog/2026/10/06/kubernetes-cgroups-v2-shift/), so Docker must provide it.

`%USERPROFILE%\.wslconfig` contains exactly:

```ini
[wsl2]
kernelCommandLine = cgroup_no_v1=all
```

- `kernelCommandLine` is a [documented WSL key](https://learn.microsoft.com/en-us/windows/wsl/wsl-config). Microsoft's page does not document this specific value.
- WSL's [2.0.1 release note](https://github.com/microsoft/WSL/releases/tag/2.0.1) and [source code](https://github.com/microsoft/WSL/blob/master/src/linux/init/config.cpp) mount cgroup v2 when the value is exactly `all`. The 2.4.13 source is not public, so this was confirmed by testing on this machine.
- The file applies to all WSL distributions. `docker-desktop` is the only one here.

Verify it with:

```powershell
docker info --format '{{.CgroupVersion}}'
docker run --rm --pull=never python:3.11-slim stat -fc %T /sys/fs/cgroup
```

The expected output is `2` and `cgroup2fs`. Do not use `stat` from an Alpine image for this check: BusyBox prints `UNKNOWN` for this filesystem.

To remove the setting, run the commands below. Kubernetes 1.37 cannot run without it, so delete the kind cluster first.

```powershell
docker desktop stop
Remove-Item "$HOME\.wslconfig"
wsl --shutdown
docker desktop start
```

A rollback rehearsal on 2026-11-01 restored the WSL configuration, kernel command line, cgroup state, containers, images, volumes, networks and Git state. Docker's reported total memory differed by 4,096 bytes; the cause was not investigated.

## Resource use

Measured with the cluster and sample workload running:

| Item | Value |
|---|---|
| kind node memory | About 730 MiB |
| kind node CPU when idle | About 0.18 CPU |
| Windows free RAM | 2.6 GB, with VS Code and browsers open |

The Docker VM holds on to file cache, so Windows free RAM is the practical limit on this machine. Avoid running other projects' stacks at the same time as the cluster.

## Ports

| Port | Use |
|---|---|
| 8000 | This project's backend |
| 5173 | This project's frontend dev server |
| Random, on 127.0.0.1 | Kubernetes API server, recorded in the kubeconfig |
| 8080 | Existing Jenkins Windows service |
| 8081 | Terraform project nginx (container currently stopped) |
| 3000 | Terraform project Grafana (container currently stopped) |
| 9090 | Terraform project Prometheus (container currently stopped) |

Milestones 8 and 9 must avoid these ports or run while those services are stopped.

## Known limitations

- Kubernetes 1.37.0 is not the latest patch release. kind v0.33.0 ships a pre-built image for 1.37.0 only.
- The cluster reports `PossibleMemoryBackedVolumesOnDisk`: memory-backed volumes, including Secrets, may be swapped to disk. Do not store real secrets in this cluster.
- Other projects' containers have not been tested on cgroup v2.
- Whether the cluster survives a Docker restart has not been tested.

## Change from the planned stack

The requirements list `httpx` for backend tests. This project uses `httpx2` because Starlette's `TestClient` now deprecates `httpx` and documents `httpx2` as its dependency.
