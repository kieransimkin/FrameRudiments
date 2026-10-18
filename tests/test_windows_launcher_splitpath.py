"""Regression: Windows PowerShell 5.1 cannot combine Split-Path -LiteralPath/-Parent.

`-LiteralPath` already selects the parent path by default.  Combining the two
is an AmbiguousParameterSet error *after* models have been downloaded.
"""

from pathlib import Path
import re
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "Run-FullPresetAnalysis.ps1"


def test_launcher_uses_compatible_literalpath_parameter_set():
    source = SCRIPT.read_text(encoding="utf-8")
    calls = re.findall(r"\bSplit-Path\s+-LiteralPath\s+[^)\r\n]+", source)
    assert len(calls) == 4, "Inspect all literal path parent-directory lookups"
    forbidden = r"-(?:Parent|Leaf|Qualifier|NoQualifier|IsAbsolute)\b"
    assert all(not re.search(forbidden, call) for call in calls), calls


def test_parent_directory_lookup_on_windows_powershell():
    # On Linux the source-contract check above still runs; on Windows execute
    # the actual PowerShell 5.1 cmdlet without bootstrapping models or runtimes.
    if sys.platform != "win32":
        return
    command = (
        '$parent = Split-Path -LiteralPath "C:\\Temp\\tesseract.exe"; '
        'if ($parent -cne "C:\\Temp") { throw "LiteralPath parent resolution failed" }'
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=25,
    )
