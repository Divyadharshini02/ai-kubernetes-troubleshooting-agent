"""Create or renew the backend's restricted kubeconfig, with an 8-hour token.

The file holds only the API server address, its public CA and the token. The token is never
printed, and the administrator's client key is never copied. See docs/M7_RBAC_SECURITY.md.

Run with: backend\\.venv\\Scripts\\python.exe scripts\\new_readonly_kubeconfig.py
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

LAB_CONTEXT = "kind-troubleshooting-lab"
NAMESPACE = "troubleshooting-agent"
SERVICE_ACCOUNT = "troubleshooting-agent"
BINDING = "troubleshooting-agent-reader"
TOKEN_DURATION = "8h"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADMIN = Path.home() / ".kube" / "config"
DEFAULT_OUTPUT = Path.home() / ".kube" / "troubleshooting-agent.kubeconfig"
RBAC_MANIFEST = "k8s/rbac/troubleshooting-agent.yaml"


class ScriptError(Exception):
    """A safe message for the user. It never contains command output that could hold a credential."""


def run_kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=60)


def _check_output_path(output: Path, admin: Path) -> None:
    resolved = output.resolve()
    if resolved.is_relative_to(REPO_ROOT):
        raise ScriptError(f"Refusing to write credentials inside the repository: {resolved}")
    if resolved in (admin.resolve(), DEFAULT_ADMIN.resolve()):
        raise ScriptError(f"Refusing to overwrite an administrator kubeconfig: {resolved}")


def _kubeconfig(server: str, certificate_authority: str, token: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": LAB_CONTEXT,
        "clusters": [{"name": LAB_CONTEXT, "cluster": {"server": server, "certificate-authority-data": certificate_authority}}],
        "users": [{"name": SERVICE_ACCOUNT, "user": {"token": token}}],
        "contexts": [{"name": LAB_CONTEXT, "context": {"cluster": LAB_CONTEXT, "user": SERVICE_ACCOUNT}}],
    }


def _read_cluster(admin_call) -> tuple[str, str]:
    """Take only the public parts of the administrator's kubeconfig."""
    result = admin_call("config", "view", "--raw", "--minify", "--context", LAB_CONTEXT, "-o", "json")
    if result.returncode != 0:
        raise ScriptError("Could not read the cluster's address from the administrator kubeconfig.")
    try:
        cluster = json.loads(result.stdout)["clusters"][0]["cluster"]
        return cluster["server"], cluster["certificate-authority-data"]
    except (ValueError, KeyError, IndexError) as exc:
        raise ScriptError("The administrator kubeconfig has no usable server address and CA.") from exc


def _create_token(admin_call) -> tuple[str, str]:
    result = admin_call(
        "--context", LAB_CONTEXT, "create", "token", SERVICE_ACCOUNT, "-n", NAMESPACE, f"--duration={TOKEN_DURATION}", "-o", "json"
    )
    if result.returncode != 0:
        raise ScriptError("Kubernetes did not issue a token for the ServiceAccount.")
    try:
        document = json.loads(result.stdout)
        return document["status"]["token"], document["status"]["expirationTimestamp"]
    except (ValueError, KeyError) as exc:
        raise ScriptError("Kubernetes returned an unexpected token response.") from exc


def _verify_restricted(path: str, run) -> None:
    """Prove the new file works and is restricted, using the file itself."""

    def can(*check: str) -> bool:
        return run("--kubeconfig", path, "auth", "can-i", *check).stdout.strip() == "yes"

    if not can("list", "pods", "--all-namespaces"):
        raise ScriptError("The new token cannot read pods. Check the ClusterRoleBinding.")
    if can("list", "secrets", "--all-namespaces") or can("delete", "pods", "--all-namespaces"):
        raise ScriptError("The new token has more access than it should, so it was not saved.")


def create_kubeconfig(output: Path, admin: Path, run=run_kubectl) -> str:
    """Write the kubeconfig and return when its token expires."""
    _check_output_path(output, admin)

    def admin_call(*args: str) -> subprocess.CompletedProcess:
        return run("--kubeconfig", str(admin), *args)

    contexts = admin_call("config", "get-contexts", "-o", "name")
    if contexts.returncode != 0 or LAB_CONTEXT not in contexts.stdout.split():
        raise ScriptError(f"Context {LAB_CONTEXT} was not found in {admin}. Nothing was changed.")
    if admin_call("--context", LAB_CONTEXT, "get", "serviceaccount", SERVICE_ACCOUNT, "-n", NAMESPACE, "-o", "name").returncode != 0:
        raise ScriptError(f"ServiceAccount {NAMESPACE}/{SERVICE_ACCOUNT} was not found. Apply {RBAC_MANIFEST} first.")
    if admin_call("--context", LAB_CONTEXT, "get", "clusterrolebinding", BINDING, "-o", "name").returncode != 0:
        raise ScriptError(f"ClusterRoleBinding {BINDING} was not found. Apply {RBAC_MANIFEST} first.")

    server, certificate_authority = _read_cluster(admin_call)
    token, expires_at = _create_token(admin_call)

    # Written beside the destination and checked first, so a bad token never replaces a good one.
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=output.parent, prefix=".new-kubeconfig-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(_kubeconfig(server, certificate_authority, token), file)
        _verify_restricted(temporary, run)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return expires_at


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create or renew the backend's restricted kubeconfig.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--admin-kubeconfig", type=Path, default=DEFAULT_ADMIN)
    args = parser.parse_args(argv)
    try:
        expires_at = create_kubeconfig(args.output, args.admin_kubeconfig)
    except ScriptError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("Error: kubectl was not found.", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("Error: kubectl did not respond in time.", file=sys.stderr)
        return 1
    print(f"Wrote {args.output}")
    print(f"The token expires at {expires_at}. Run this script again to renew it, with no backend restart.")
    print(f'Start the backend with: $env:KUBECONFIG = "{args.output}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
