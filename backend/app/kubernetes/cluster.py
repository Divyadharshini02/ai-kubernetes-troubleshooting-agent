"""Read-only access to the cluster selected by the current kubeconfig."""

import logging
import os
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from app.models.cluster import ClusterHealth, NodeSummary, PodSummary

logger = logging.getLogger(__name__)

# (connect, read) seconds for each Kubernetes API request.
REQUEST_TIMEOUT_SECONDS = (3, 10)
LIST_LIMIT = 500


class ClusterAccessError(Exception):
    """A safe-to-display cluster failure.

    The message must never contain raw exception text, because client errors
    can include request headers and response bodies.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def is_tls_failure(exc: ApiException) -> bool:
    """Tell a TLS failure apart from the client's other status 0 errors.

    The client reports TLS failures as status 0 with the error type first in the
    reason. It also uses status 0 for responses with an unsupported content type.
    """
    return not exc.status and str(exc.reason).startswith("SSLError")


def load_configuration() -> tuple[client.Configuration, str]:
    # The client reads KUBECONFIG once at import, so read it per call instead.
    config_file = os.environ.get("KUBECONFIG")
    configuration = client.Configuration()
    # A failed request should fail fast instead of retrying with the default policy.
    configuration.retries = False
    try:
        config.load_kube_config(client_configuration=configuration, config_file=config_file)
        _, active_context = config.list_kube_config_contexts(config_file=config_file)
    # The kubeconfig parser raises many unrelated types for a damaged file.
    except Exception as exc:
        logger.warning("Could not load kubeconfig: %s", type(exc).__name__)
        raise ClusterAccessError(
            "kubeconfig_unavailable",
            "The Kubernetes configuration is missing or invalid.",
        ) from exc
    return configuration, active_context["name"]


def _node_summary(node: client.V1Node) -> NodeSummary:
    conditions = node.status.conditions if node.status and node.status.conditions else []
    ready_status = next((c.status for c in conditions if c.type == "Ready"), None)
    node_info = node.status.node_info if node.status else None
    return NodeSummary(
        name=node.metadata.name,
        ready=None if ready_status in (None, "Unknown") else ready_status == "True",
        kubelet_version=node_info.kubelet_version if node_info else None,
    )


def _pod_summary(pod: client.V1Pod) -> PodSummary:
    statuses = (pod.status.container_statuses or []) if pod.status else []
    return PodSummary(
        namespace=pod.metadata.namespace,
        name=pod.metadata.name,
        phase=pod.status.phase if pod.status else None,
        node=pod.spec.node_name,
        ready_containers=sum(1 for status in statuses if status.ready),
        total_containers=len(pod.spec.containers),
        restarts=sum(status.restart_count for status in statuses),
    )


def read_cluster_health() -> ClusterHealth:
    """Read real node and pod information. Raises ClusterAccessError on any failure."""
    configuration, context = load_configuration()
    try:
        with client.ApiClient(configuration) as api_client:
            version = client.VersionApi(api_client).get_code(
                _request_timeout=REQUEST_TIMEOUT_SECONDS
            )
            core = client.CoreV1Api(api_client)
            nodes = core.list_node(limit=LIST_LIMIT, _request_timeout=REQUEST_TIMEOUT_SECONDS)
            pods = core.list_pod_for_all_namespaces(
                limit=LIST_LIMIT, _request_timeout=REQUEST_TIMEOUT_SECONDS
            )
            return ClusterHealth(
                status="connected",
                context=context,
                server_version=version.git_version,
                collected_at=datetime.now(timezone.utc),
                truncated=bool(nodes.metadata._continue or pods.metadata._continue),
                nodes=[_node_summary(node) for node in nodes.items],
                pods=[_pod_summary(pod) for pod in pods.items],
            )
    except ApiException as exc:
        if is_tls_failure(exc):
            logger.warning("Could not establish a secure connection to the Kubernetes API")
            raise ClusterAccessError(
                "cluster_unreachable",
                "A secure connection to the Kubernetes API could not be established.",
            ) from exc
        if not exc.status:
            logger.warning("Kubernetes API response could not be processed")
            raise ClusterAccessError(
                "cluster_api_error",
                "The Kubernetes API returned an unexpected response.",
            ) from exc
        logger.warning("Kubernetes API returned status %s", exc.status)
        if exc.status in (401, 403):
            raise ClusterAccessError(
                "cluster_access_denied",
                "The Kubernetes API rejected the configured credentials or permissions.",
            ) from exc
        raise ClusterAccessError(
            "cluster_api_error",
            f"The Kubernetes API returned an error (HTTP {exc.status}).",
        ) from exc
    except HTTPError as exc:
        logger.warning("Could not reach the Kubernetes API: %s", type(exc).__name__)
        raise ClusterAccessError(
            "cluster_unreachable",
            "The Kubernetes API could not be reached.",
        ) from exc
    # The client raises ValueError when a response does not match the expected schema.
    except ValueError as exc:
        logger.warning("Unexpected Kubernetes API response: %s", type(exc).__name__)
        raise ClusterAccessError(
            "cluster_api_error",
            "The Kubernetes API returned an unexpected response.",
        ) from exc
