"""The engine files and both original dashboards are byte-for-byte what was handed over."""
import hashlib
import os

from helpers import ROOT


def test_files_match_recorded_hashes():
    lines = [l.split() for l in open(os.path.join(ROOT, "ENGINE_HASHES.txt")).read().splitlines() if l.strip()]
    assert len(lines) == 5
    for digest, rel in lines:
        got = hashlib.sha256(open(os.path.join(ROOT, rel), "rb").read()).hexdigest()
        assert got == digest, f"{rel} has been edited"
