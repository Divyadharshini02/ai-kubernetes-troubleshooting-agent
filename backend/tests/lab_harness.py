"""Creates and removes the throwaway evidence-lab namespace for cluster tests.

The harness writes to the cluster using kubectl. The application never does.

Cleanup guarantees:
- Normal exit, test failure, a failed apply and KeyboardInterrupt all remove the lab.
- A hard kill cannot run cleanup, so every new lab first removes any leftover one.
- Removal is verified, and a failure to remove the lab raises an error.

Safety guards, all checked before anything is written:
- Writes use the kubeconfig in TEST_ADMIN_KUBECONFIG, never the backend's in KUBECONFIG.
- The current kubeconfig context must be the local kind cluster.
- An existing namespace is only removed if it carries this harness's label.
"""

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

LAB_NAMESPACE = "evidence-lab"
LAB_CONTEXT = "kind-troubleshooting-lab"
ADMIN_KUBECONFIG = "TEST_ADMIN_KUBECONFIG"
OWNER_LABEL = "app.kubernetes.io/managed-by"
OWNER_VALUE = "evidence-lab-tests"
MANIFEST = Path(__file__).parent / "fixtures" / "evidence-lab.yaml"
MANUAL_CLEANUP = f"kubectl --context {LAB_CONTEXT} delete namespace {LAB_NAMESPACE}"


class LabSafetyError(Exception):
    """The harness refused to write, to protect something it does not own."""


class LabCleanupError(Exception):
    """The lab namespace could not be removed."""


def admin_kubeconfig() -> str:
    """The kubeconfig for the harnesses' writes, which must never be the backend's identity."""
    admin = os.environ.get(ADMIN_KUBECONFIG)
    if not admin:
        raise LabSafetyError(f"Set {ADMIN_KUBECONFIG} to an administrator kubeconfig. Nothing was changed.")
    backend = os.environ.get("KUBECONFIG")
    if backend and Path(backend).resolve() == Path(admin).resolve():
        raise LabSafetyError(f"{ADMIN_KUBECONFIG} and KUBECONFIG are the same file. Nothing was changed.")
    return admin


def run_kubectl(*args: str) -> str:
    # Every lab command names the context, so a later context switch cannot redirect it.
    result = subprocess.run(
        ["kubectl", "--kubeconfig", admin_kubeconfig(), "--context", LAB_CONTEXT, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return result.stdout


def current_context() -> str:
    result = subprocess.run(
        ["kubectl", "--kubeconfig", admin_kubeconfig(), "config", "current-context"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _read_namespace(kubectl) -> dict | None:
    output = kubectl("get", "namespace", LAB_NAMESPACE, "--ignore-not-found", "-o", "json")
    return json.loads(output) if output.strip() else None


def namespace_exists(kubectl=run_kubectl) -> bool:
    return _read_namespace(kubectl) is not None


def remove_lab(kubectl=run_kubectl) -> None:
    """Delete the lab namespace and confirm it is gone."""
    try:
        kubectl("delete", "namespace", LAB_NAMESPACE, "--ignore-not-found", "--wait=true", "--timeout=120s")
        still_there = namespace_exists(kubectl)
    except Exception as exc:
        raise LabCleanupError(f"Could not remove the lab. Run: {MANUAL_CLEANUP}") from exc
    if still_there:
        raise LabCleanupError(f"The lab namespace still exists. Run: {MANUAL_CLEANUP}")


def _remove_leftover_lab(kubectl) -> None:
    existing = _read_namespace(kubectl)
    if existing is None:
        return
    labels = existing.get("metadata", {}).get("labels") or {}
    if labels.get(OWNER_LABEL) != OWNER_VALUE:
        raise LabSafetyError(
            f"Namespace {LAB_NAMESPACE} exists without the {OWNER_LABEL}={OWNER_VALUE} label, "
            "so these tests did not create it. It was left untouched."
        )
    remove_lab(kubectl)


@contextmanager
def evidence_lab(kubectl=run_kubectl, get_context=current_context):
    """Provide the lab namespace for the duration of a with block."""
    context = get_context()
    # The application reads whichever cluster the current context selects.
    if context != LAB_CONTEXT:
        raise LabSafetyError(
            f"The current context is {context!r}, not {LAB_CONTEXT!r}. Nothing was changed."
        )
    _remove_leftover_lab(kubectl)
    try:
        kubectl("apply", "-f", str(MANIFEST))
        yield
    finally:
        remove_lab(kubectl)
