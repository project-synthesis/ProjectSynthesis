"""
CI grep guard hook tests — Task 27.

RED: hook file does not yet exist → tests fail.
GREEN: hook created and chmod +x → tests pass.
"""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "pre-pr-template-guard.sh"


def test_template_guard_hook_exists_and_executable():
    assert HOOK.is_file(), f"Hook missing: {HOOK}"
    assert os.access(HOOK, os.X_OK), f"Hook not executable: {HOOK}"


def test_template_guard_passes_on_current_tree():
    result = subprocess.run(
        [str(HOOK)], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, (
        f"Guard blocked on clean tree:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
