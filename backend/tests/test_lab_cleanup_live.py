"""Proves lab cleanup in separate real processes. Run with: pytest -m cluster

Each test starts its own Python process, so a real failure, interrupt or hard exit
happens there. Each one also proves the lab existed before checking it was removed.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lab_harness import evidence_lab, namespace_exists, remove_lab

pytestmark = pytest.mark.cluster

TESTS_DIR = Path(__file__).parent

PYTEST_CHILD = """
    from pathlib import Path

    import pytest

    from lab_harness import evidence_lab, namespace_exists


    @pytest.fixture(scope="module")
    def lab():
        with evidence_lab():
            yield


    def test_child(lab):
        assert namespace_exists()
        Path("lab-was-created").write_text("yes")
        {action}
"""

HARD_EXIT_CHILD = """
    import os
    from pathlib import Path

    from lab_harness import evidence_lab, namespace_exists

    with evidence_lab():
        assert namespace_exists()
        Path("lab-was-created").write_text("yes")
        os._exit(9)
"""


def run_child(tmp_path, *command):
    return subprocess.run(
        [sys.executable, *command],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(TESTS_DIR)},
        capture_output=True,
        text=True,
        timeout=300,
    )


def run_pytest_child(tmp_path, action):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    source = textwrap.dedent(PYTEST_CHILD).format(action=action)
    (tmp_path / "test_child.py").write_text(source)
    return run_child(tmp_path, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_child.py")


@pytest.fixture(autouse=True)
def never_leave_the_lab_behind():
    yield
    remove_lab()


def test_failing_test_removes_the_lab(tmp_path):
    result = run_pytest_child(tmp_path, "assert False, 'deliberate failure'")

    assert (tmp_path / "lab-was-created").exists(), result.stdout
    assert result.returncode == 1, result.stdout
    assert not namespace_exists()


def test_keyboard_interrupt_removes_the_lab(tmp_path):
    result = run_pytest_child(tmp_path, "raise KeyboardInterrupt")

    assert (tmp_path / "lab-was-created").exists(), result.stdout
    assert result.returncode == 2, result.stdout
    assert not namespace_exists()


def test_hard_exit_leaves_a_lab_that_the_next_run_removes(tmp_path):
    result = run_child(tmp_path, "-c", textwrap.dedent(HARD_EXIT_CHILD))

    assert (tmp_path / "lab-was-created").exists(), result.stderr
    assert result.returncode == 9, result.stderr
    # A hard exit cannot run cleanup, so the lab is expected to remain here.
    assert namespace_exists()

    with evidence_lab():
        assert namespace_exists()

    assert not namespace_exists()
