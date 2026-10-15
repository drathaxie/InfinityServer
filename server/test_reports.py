"""Report destinations for replay scripts; routine checks never rewrite tracked documents."""
import os
from pathlib import Path
import tempfile


def report_path(name):
    target = Path(os.environ.get("INFINITY_TEST_REPORT_DIR", Path(tempfile.gettempdir()) / "infinity-test-reports"))
    target.mkdir(parents=True, exist_ok=True)
    return target / name
