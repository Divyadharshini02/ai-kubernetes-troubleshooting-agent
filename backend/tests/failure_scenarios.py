"""Applies and removes the failure-demo scenarios for the tests. The application never does.

Safety guards, all checked before anything is written:
- The current kubeconfig context must be the local kind cluster.
- An existing failure-demo namespace must carry the M6 ownership label.
- Only a named file in k8s/scenarios can be applied, one at a time.
"""

import json
from contextlib import contextmanager
from pathlib import Path

from lab_harness import LAB_CONTEXT, LabCleanupError, LabSafetyError, current_context, run_kubectl

NAMESPACE = "failure-demo"
OWNER_LABEL = "app.kubernetes.io/managed-by"
OWNER_VALUE = "failure-scenarios"
SCENARIOS = Path(__file__).resolve().parents[2] / "k8s" / "scenarios"
HEALTHY = "healthy.yaml"
FAULTS = ("crash-loop.yaml", "image-pull.yaml", "pending-pod.yaml", "readiness-probe.yaml", "service-selector.yaml")
MANUAL_CLEANUP = f"kubectl --context {LAB_CONTEXT} delete namespace {NAMESPACE}"


def require_lab_context(get_context=current_context) -> None:
    context = get_context()
    if context != LAB_CONTEXT:
        raise LabSafetyError(f"The current context is {context!r}, not {LAB_CONTEXT!r}. Nothing was changed.")


def _read_namespace(kubectl) -> dict | None:
    output = kubectl("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json")
    return json.loads(output) if output.strip() else None


def require_ownership(kubectl=run_kubectl) -> None:
    """Refuse to touch a failure-demo namespace these scenarios did not create."""
    existing = _read_namespace(kubectl)
    if existing is None:
        return
    labels = existing.get("metadata", {}).get("labels") or {}
    if labels.get(OWNER_LABEL) != OWNER_VALUE:
        raise LabSafetyError(
            f"Namespace {NAMESPACE} exists without the {OWNER_LABEL}={OWNER_VALUE} label, "
            "so these scenarios did not create it. It was left untouched."
        )


def apply_scenario(name: str, kubectl=run_kubectl, *flags: str) -> None:
    if name != HEALTHY and name not in FAULTS:
        raise ValueError(f"Unknown scenario manifest: {name!r}")
    kubectl("apply", *flags, "-f", str(SCENARIOS / name))


def remove_scenarios(kubectl=run_kubectl) -> None:
    """Delete the namespace if it is ours, and confirm it is gone."""
    require_ownership(kubectl)
    try:
        kubectl("delete", "namespace", NAMESPACE, "--ignore-not-found", "--wait=true", "--timeout=120s")
        still_there = _read_namespace(kubectl) is not None
    except Exception as exc:
        raise LabCleanupError(f"Could not remove the namespace. Run: {MANUAL_CLEANUP}") from exc
    if still_there:
        raise LabCleanupError(f"The namespace still exists. Run: {MANUAL_CLEANUP}")


@contextmanager
def failure_demo(kubectl=run_kubectl, get_context=current_context):
    """Provide the healthy baseline for a with block, and always remove it afterwards."""
    require_lab_context(get_context)
    require_ownership(kubectl)
    try:
        apply_scenario(HEALTHY, kubectl)
        yield
    finally:
        remove_scenarios(kubectl)
