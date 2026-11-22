import subprocess
import sys

import pompy


def test_version():
    assert pompy.__version__


def test_cli_runs():
    result = subprocess.run(
        [sys.executable, "-m", "pompy.cli", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "pompy" in result.stdout
