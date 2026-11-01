import pytest

from app.collectors.namespaces import collect_namespace
from app.collectors.pods import collect_pods, parse_image_reference
from stub_kube import NAMESPACE, SECRET_VALUE, by_id, container_status, list_of, make_pod

PODS = f"/api/v1/namespaces/{NAMESPACE}/pods"


def collect(ctx, kube, *pods):
    kube.routes[PODS] = (200, list_of("PodList", list(pods)))
    return by_id(collect_pods(ctx))


def test_running_pod_reports_current_state(ctx, kube):
    pod = make_pod(
        statuses=[container_status("app")],
        labels={"app": "web"},
        ownerReferences=[{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": "rs", "uid": "u"}],
        status_extra={
            "conditions": [{"type": "Ready", "status": "True"}],
            "startTime": "2026-01-01T00:00:00Z",
        },
    )

    records = collect(ctx, kube, pod)

    record = records[f"pod:{NAMESPACE}/web"]
    assert record.temporality == "current"
    assert record.status == "collected"
    assert record.resource_kind == "Pod"
    assert record.namespace == NAMESPACE
    assert record.data["phase"] == "Running"
    assert record.data["labels"] == {"app": "web"}
    assert record.data["owner_references"] == [{"kind": "ReplicaSet", "name": "rs"}]
    assert record.data["conditions"][0]["type"] == "Ready"

    container = records[f"container:{NAMESPACE}/web/app"]
    assert container.temporality == "current"
    assert container.data["state"]["kind"] == "running"
    assert container.data["ready"] is True
    assert container.data["restart_count"] == 0
    assert container.data["has_last_termination"] is False
    assert f"container-last-state:{NAMESPACE}/web/app" not in records


def test_crash_loop_separates_current_state_from_last_termination(ctx, kube):
    pod = make_pod(
        statuses=[
            container_status(
                "app",
                ready=False,
                restarts=4,
                state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off restarting"}},
                last_state={
                    "terminated": {
                        "exitCode": 137,
                        "reason": "OOMKilled",
                        "startedAt": "2026-01-01T00:00:00Z",
                        "finishedAt": "2026-01-01T00:01:00Z",
                    }
                },
            )
        ]
    )

    records = collect(ctx, kube, pod)

    current = records[f"container:{NAMESPACE}/web/app"]
    assert current.temporality == "current"
    assert current.data["state"] == {
        "kind": "waiting",
        "reason": "CrashLoopBackOff",
        "message": "back-off restarting",
    }
    assert current.data["restart_count"] == 4
    assert current.data["has_last_termination"] is True

    last = records[f"container-last-state:{NAMESPACE}/web/app"]
    assert last.temporality == "historical"
    assert last.observed_at is not None
    assert last.data["last_state"]["exit_code"] == 137
    assert last.data["last_state"]["reason"] == "OOMKilled"


def test_pending_pod_has_no_guessed_container_state(ctx, kube):
    pod = make_pod(
        phase="Pending",
        status_extra={
            "conditions": [
                {
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "0/1 nodes are available",
                }
            ]
        },
    )

    records = collect(ctx, kube, pod)

    assert records[f"pod:{NAMESPACE}/web"].data["node_name"] is None
    container = records[f"container:{NAMESPACE}/web/app"]
    assert container.data["status_reported"] is False
    assert container.data["state"] is None
    assert container.data["ready"] is None
    assert container.data["restart_count"] is None


def test_image_pull_failure_keeps_image_and_reason(ctx, kube):
    pod = make_pod(
        phase="Pending",
        spec={
            "containers": [
                {"name": "app", "image": "registry.example/team/app:missing", "imagePullPolicy": "Always"}
            ],
            "imagePullSecrets": [{"name": "registry-login"}],
        },
        statuses=[
            container_status(
                "app",
                ready=False,
                started=False,
                state={"waiting": {"reason": "ImagePullBackOff", "message": "pull failed"}},
            )
        ],
    )

    records = collect(ctx, kube, pod)

    container = records[f"container:{NAMESPACE}/web/app"].data
    assert container["state"]["reason"] == "ImagePullBackOff"
    assert container["image_pull_policy"] == "Always"
    assert container["image"] == {
        "reference": "registry.example/team/app:missing",
        "registry": "registry.example",
        "repository": "team/app",
        "tag": "missing",
        "digest": None,
    }
    assert records[f"pod:{NAMESPACE}/web"].data["image_pull_secrets"] == ["registry-login"]


def test_scheduling_configuration_is_collected(ctx, kube):
    affinity = {
        "podAntiAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": [
                {"topologyKey": "kubernetes.io/hostname", "labelSelector": {"matchLabels": {"app": "x"}}}
            ]
        }
    }
    pod = make_pod(
        phase="Pending",
        spec={
            "containers": [
                {
                    "name": "app",
                    "image": "i",
                    "resources": {"requests": {"cpu": "100"}, "limits": {"memory": "1Gi"}},
                }
            ],
            "nodeSelector": {"disk": "ssd"},
            "tolerations": [{"key": "dedicated", "operator": "Exists", "effect": "NoSchedule"}],
            "affinity": affinity,
            "volumes": [
                {"name": "data", "persistentVolumeClaim": {"claimName": "data-claim"}},
                {"name": "scratch", "emptyDir": {}},
            ],
        },
    )

    records = collect(ctx, kube, pod)

    data = records[f"pod:{NAMESPACE}/web"].data
    assert data["node_selector"] == {"disk": "ssd"}
    assert data["tolerations"][0]["key"] == "dedicated"
    assert data["affinity"]["podAntiAffinity"]
    assert data["persistent_volume_claims"] == ["data-claim"]
    container = records[f"container:{NAMESPACE}/web/app"].data
    assert container["resources"] == {"requests": {"cpu": "100"}, "limits": {"memory": "1Gi"}}


def test_probe_configuration_is_collected_without_secrets(ctx, kube):
    timing = {"initialDelaySeconds": 5, "periodSeconds": 7, "timeoutSeconds": 2, "failureThreshold": 3}
    pod = make_pod(
        spec={
            "containers": [
                {
                    "name": "app",
                    "image": "i",
                    "readinessProbe": {
                        "httpGet": {
                            "path": "/ready",
                            "port": "http",
                            "scheme": "HTTP",
                            "httpHeaders": [{"name": "X-Auth", "value": SECRET_VALUE}],
                        },
                        **timing,
                    },
                    "livenessProbe": {
                        "exec": {"command": ["check", f"--password={SECRET_VALUE}"]},
                        **timing,
                    },
                    "startupProbe": {"tcpSocket": {"port": 8080}, **timing},
                }
            ]
        }
    )

    records = collect(ctx, kube, pod)

    container = records[f"container:{NAMESPACE}/web/app"]
    probes = container.data["probes"]
    assert probes["readiness"]["handler"] == {
        "type": "http_get",
        "path": "/ready",
        "port": "http",
        "scheme": "HTTP",
        "header_names": ["X-Auth"],
    }
    assert probes["readiness"]["initial_delay_seconds"] == 5
    assert probes["readiness"]["period_seconds"] == 7
    assert probes["readiness"]["timeout_seconds"] == 2
    assert probes["readiness"]["failure_threshold"] == 3
    assert probes["liveness"]["handler"]["type"] == "exec"
    assert probes["startup"]["handler"] == {"type": "tcp_socket", "port": 8080}
    assert container.redactions == 1
    assert SECRET_VALUE not in container.model_dump_json()


def test_missing_probes_stay_empty(ctx, kube):
    records = collect(ctx, kube, make_pod())

    assert records[f"container:{NAMESPACE}/web/app"].data["probes"] == {
        "readiness": None,
        "liveness": None,
        "startup": None,
    }


def test_environment_exposes_names_and_references_but_never_values(ctx, kube):
    pod = make_pod(
        annotations={"kubectl.kubernetes.io/last-applied-configuration": SECRET_VALUE},
        spec={
            "containers": [
                {
                    "name": "app",
                    "image": "i",
                    "command": ["run", f"--token={SECRET_VALUE}"],
                    "args": [SECRET_VALUE],
                    "env": [
                        {"name": "PLAIN", "value": SECRET_VALUE},
                        {
                            "name": "FROM_SECRET",
                            "valueFrom": {"secretKeyRef": {"name": "db", "key": "password"}},
                        },
                        {
                            "name": "FROM_CONFIG",
                            "valueFrom": {"configMapKeyRef": {"name": "cfg", "key": "url", "optional": True}},
                        },
                        {
                            "name": "POD_IP",
                            "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
                        },
                    ],
                    "envFrom": [
                        {"secretRef": {"name": "all-secrets"}},
                        {"configMapRef": {"name": "all-config"}},
                    ],
                }
            ]
        },
    )

    records = collect(ctx, kube, pod)

    env = records[f"container:{NAMESPACE}/web/app"].data["env"]
    assert env["variables"] == [
        {"name": "PLAIN", "source": "literal"},
        {"name": "FROM_SECRET", "source": "secret", "secret_name": "db", "key": "password", "optional": None},
        {"name": "FROM_CONFIG", "source": "config_map", "config_map_name": "cfg", "key": "url", "optional": True},
        {"name": "POD_IP", "source": "field", "field_path": "status.podIP"},
    ]
    assert env["from"] == [
        {"kind": "Secret", "name": "all-secrets"},
        {"kind": "ConfigMap", "name": "all-config"},
    ]
    for record in records.values():
        assert SECRET_VALUE not in record.model_dump_json()


def test_status_messages_are_redacted(ctx, kube):
    pod = make_pod(
        statuses=[
            container_status(
                "app",
                ready=False,
                state={"waiting": {"reason": "CreateContainerError", "message": f"token={SECRET_VALUE}"}},
            )
        ],
        status_extra={"message": f"password={SECRET_VALUE}"},
    )

    records = collect(ctx, kube, pod)

    assert records[f"pod:{NAMESPACE}/web"].redactions == 1
    assert records[f"container:{NAMESPACE}/web/app"].redactions == 1
    for record in records.values():
        assert SECRET_VALUE not in record.model_dump_json()


def test_init_containers_are_labelled(ctx, kube):
    pod = make_pod(
        spec={
            "initContainers": [{"name": "setup", "image": "i"}],
            "containers": [{"name": "app", "image": "i"}],
        },
        status_extra={
            "initContainerStatuses": [
                container_status(
                    "setup",
                    ready=False,
                    state={"terminated": {"exitCode": 1, "reason": "Error"}},
                )
            ]
        },
    )

    records = collect(ctx, kube, pod)

    setup = records[f"container:{NAMESPACE}/web/setup"].data
    assert setup["container_type"] == "init"
    assert setup["state"]["exit_code"] == 1
    assert records[f"container:{NAMESPACE}/web/app"].data["container_type"] == "app"


@pytest.mark.parametrize(
    ("image", "registry", "repository", "tag", "digest"),
    [
        ("nginx", None, "nginx", None, None),
        ("nginx:1.27", None, "nginx", "1.27", None),
        ("team/app:1", None, "team/app", "1", None),
        ("ghcr.io/team/app:1", "ghcr.io", "team/app", "1", None),
        ("localhost:5000/app", "localhost:5000", "app", None, None),
        ("localhost/app:2", "localhost", "app", "2", None),
        ("repo/app@sha256:abc", None, "repo/app", None, "sha256:abc"),
        ("nginx:1.27@sha256:abc", None, "nginx", "1.27", "sha256:abc"),
    ],
)
def test_image_reference_parsing(image, registry, repository, tag, digest):
    assert parse_image_reference(image) == {
        "reference": image,
        "registry": registry,
        "repository": repository,
        "tag": tag,
        "digest": digest,
    }


def test_namespace_collector_reports_phase(ctx, kube):
    kube.routes[f"/api/v1/namespaces/{NAMESPACE}"] = (
        200,
        {"metadata": {"name": NAMESPACE, "labels": {"a": "b"}}, "status": {"phase": "Terminating"}},
    )

    record = collect_namespace(ctx)

    assert record.status == "collected"
    assert record.data == {"phase": "Terminating", "labels": {"a": "b"}}


@pytest.mark.parametrize(
    ("http_status", "status"),
    [(404, "not_found"), (403, "forbidden"), (500, "failed")],
)
def test_namespace_collector_reports_failures(ctx, kube, http_status, status):
    kube.routes[f"/api/v1/namespaces/{NAMESPACE}"] = (http_status, {"kind": "Status"})

    record = collect_namespace(ctx)

    assert record.status == status
    assert record.error is not None
