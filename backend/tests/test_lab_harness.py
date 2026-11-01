"""Harness lifecycle tests that need no cluster, using a fake kubectl."""

import json
import subprocess

import pytest
import yaml

from lab_harness import (
    LAB_CONTEXT,
    LAB_NAMESPACE,
    MANIFEST,
    OWNER_LABEL,
    OWNER_VALUE,
    LabCleanupError,
    LabSafetyError,
    evidence_lab,
    remove_lab,
)

OWNED = {"metadata": {"name": LAB_NAMESPACE, "labels": {OWNER_LABEL: OWNER_VALUE}}}
NOT_OWNED = {"metadata": {"name": LAB_NAMESPACE, "labels": {"team": "someone-else"}}}


class FakeKubectl:
    """Tracks whether the namespace exists, like a very small cluster."""

    def __init__(self, existing=None, fail_on=None, delete_works=True):
        self.namespace = existing
        self.fail_on = fail_on
        self.delete_works = delete_works
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args[0])
        if args[0] == self.fail_on:
            raise subprocess.CalledProcessError(1, "kubectl")
        if args[0] == "get":
            return json.dumps(self.namespace) if self.namespace else ""
        if args[0] == "apply":
            self.namespace = OWNED
        if args[0] == "delete" and self.delete_works:
            self.namespace = None
        return ""


def correct_context():
    return LAB_CONTEXT


def test_lab_exists_inside_the_block_and_is_removed_after():
    kubectl = FakeKubectl()

    with evidence_lab(kubectl, correct_context):
        assert kubectl.namespace is not None

    assert kubectl.namespace is None


@pytest.mark.parametrize("error", [AssertionError, RuntimeError, KeyboardInterrupt, SystemExit])
def test_lab_is_removed_when_the_block_is_interrupted(error):
    kubectl = FakeKubectl()

    with pytest.raises(error):
        with evidence_lab(kubectl, correct_context):
            raise error()

    assert kubectl.namespace is None


def test_lab_is_removed_when_apply_fails_partway():
    kubectl = FakeKubectl(fail_on="apply")

    with pytest.raises(subprocess.CalledProcessError):
        with evidence_lab(kubectl, correct_context):
            pytest.fail("the block must not run when apply fails")

    assert "delete" in kubectl.calls


def test_interrupt_during_apply_still_removes_the_lab():
    kubectl = FakeKubectl()
    real_call = kubectl.__call__

    def interrupted(*args):
        output = real_call(*args)
        if args[0] == "apply":
            raise KeyboardInterrupt
        return output

    with pytest.raises(KeyboardInterrupt):
        with evidence_lab(interrupted, correct_context):
            pytest.fail("the block must not run")

    assert kubectl.namespace is None


def test_leftover_lab_from_a_killed_run_is_removed_before_apply():
    kubectl = FakeKubectl(existing=OWNED)

    with evidence_lab(kubectl, correct_context):
        pass

    assert kubectl.calls.index("delete") < kubectl.calls.index("apply")
    assert kubectl.namespace is None


@pytest.mark.parametrize("existing", [NOT_OWNED, {"metadata": {"name": LAB_NAMESPACE}}])
def test_namespace_without_the_label_is_never_touched(existing):
    kubectl = FakeKubectl(existing=existing)

    with pytest.raises(LabSafetyError):
        with evidence_lab(kubectl, correct_context):
            pytest.fail("the block must not run")

    assert kubectl.namespace == existing
    assert "delete" not in kubectl.calls
    assert "apply" not in kubectl.calls


def test_wrong_cluster_context_changes_nothing():
    kubectl = FakeKubectl()

    with pytest.raises(LabSafetyError):
        with evidence_lab(kubectl, lambda: "production-cluster"):
            pytest.fail("the block must not run")

    assert kubectl.calls == []


def test_failed_delete_is_reported():
    kubectl = FakeKubectl(existing=OWNED, fail_on="delete")

    with pytest.raises(LabCleanupError):
        remove_lab(kubectl)


def test_namespace_that_survives_delete_is_reported():
    kubectl = FakeKubectl(existing=OWNED, delete_works=False)

    with pytest.raises(LabCleanupError):
        remove_lab(kubectl)


def test_cleanup_failure_after_a_passing_block_is_not_hidden():
    kubectl = FakeKubectl(delete_works=False)

    with pytest.raises(LabCleanupError):
        with evidence_lab(kubectl, correct_context):
            pass


def test_removing_an_absent_lab_succeeds():
    remove_lab(FakeKubectl())


def manifest_documents():
    return [doc for doc in yaml.safe_load_all(MANIFEST.read_text()) if doc]


def test_namespace_in_the_manifest_carries_the_owner_label():
    namespaces = [doc for doc in manifest_documents() if doc["kind"] == "Namespace"]

    assert len(namespaces) == 1
    assert namespaces[0]["metadata"]["name"] == LAB_NAMESPACE
    assert namespaces[0]["metadata"]["labels"][OWNER_LABEL] == OWNER_VALUE


def test_every_resource_is_removed_with_the_namespace():
    # Anything cluster-scoped or in another namespace would survive namespace deletion.
    allowed_kinds = {"Deployment", "Pod", "Service", "PersistentVolumeClaim"}

    for doc in manifest_documents():
        if doc["kind"] == "Namespace":
            continue
        assert doc["kind"] in allowed_kinds
        assert doc["metadata"]["namespace"] == LAB_NAMESPACE
