"""A local HTTP server that stands in for the Kubernetes API in unit tests.

The real Kubernetes client talks to it, so client parsing and errors are exercised.
It answers only from data each test provides and is never used by production code.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

NAMESPACE = "demo"
SECRET_VALUE = "test-only-secret-value"


class StubKube:
    def __init__(self):
        # path -> (status, body) or a callable(query) returning (status, body).
        # bytes are sent as text/plain, str as raw JSON and anything else as JSON.
        # An optional third item overrides the content type.
        self.routes = {}
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _respond(self):
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/")
                query = parse_qs(parsed.query)
                stub.requests.append((self.command, path, query))
                route = stub.routes.get(path, (404, {"kind": "Status", "code": 404}))
                status, body, *content_type_override = route(query) if callable(route) else route
                if isinstance(body, bytes):
                    payload, content_type = body, "text/plain"
                elif isinstance(body, str):
                    payload, content_type = body.encode(), "application/json"
                else:
                    payload, content_type = json.dumps(body).encode(), "application/json"
                self.send_response(status)
                self.send_header("Content-Type", (content_type_override or [content_type])[0])
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _respond

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        # A short poll interval keeps server shutdown, and so each test, fast.
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def list_of(kind, items, continue_token=None):
    metadata = {"continue": continue_token} if continue_token else {}
    return {"kind": kind, "apiVersion": "v1", "metadata": metadata, "items": items}


def metadata(name, namespace=NAMESPACE, **extra):
    return {"name": name, "namespace": namespace, "uid": f"uid-{name}", **extra}


def container_status(name, *, ready=True, restarts=0, state=None, last_state=None, started=True):
    status = {
        "name": name,
        "image": "registry.example/app:1",
        "imageID": "registry.example/app@sha256:abc",
        "ready": ready,
        "started": started,
        "restartCount": restarts,
        "state": state if state is not None else {"running": {"startedAt": "2026-01-01T00:00:00Z"}},
    }
    if last_state:
        status["lastState"] = last_state
    return status


def make_pod(name="web", *, phase="Running", spec=None, statuses=None, status_extra=None, **meta):
    pod_status = {"phase": phase, **(status_extra or {})}
    if statuses is not None:
        pod_status["containerStatuses"] = statuses
    return {
        "metadata": metadata(name, **meta),
        "spec": spec or {"containers": [{"name": "app", "image": "registry.example/app:1"}]},
        "status": pod_status,
    }


def pod_log_path(pod):
    return f"/api/v1/namespaces/{NAMESPACE}/pods/{pod}/log"


def namespace_routes():
    """Routes for a namespace that exists and holds no resources of any kind."""
    base = f"/api/v1/namespaces/{NAMESPACE}"
    apps = f"/apis/apps/v1/namespaces/{NAMESPACE}"
    return {
        base: (200, {"metadata": {"name": NAMESPACE}, "status": {"phase": "Active"}}),
        f"{base}/pods": (200, list_of("PodList", [])),
        f"{base}/events": (200, list_of("EventList", [])),
        f"{apps}/deployments": (200, list_of("DeploymentList", [])),
        f"{apps}/replicasets": (200, list_of("ReplicaSetList", [])),
        f"{base}/services": (200, list_of("ServiceList", [])),
        f"/apis/discovery.k8s.io/v1/namespaces/{NAMESPACE}/endpointslices": (
            200,
            list_of("EndpointSliceList", []),
        ),
        "/api/v1/nodes": (200, list_of("NodeList", [])),
        f"{base}/persistentvolumeclaims": (200, list_of("PersistentVolumeClaimList", [])),
        "/apis/storage.k8s.io/v1/storageclasses": (200, list_of("StorageClassList", [])),
        f"{base}/serviceaccounts": (200, list_of("ServiceAccountList", [])),
    }


def by_id(records):
    return {record.evidence_id: record for record in records}
