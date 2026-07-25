"""Make the backend package importable to voice-gw's tests.

voice-gw runs the shared `app.intake.IntakeEngine` in-process (S14), so its tests
import both `gw.*` and `app.*`. `make test-voicegw` runs them on the backend venv
with `PYTHONPATH` set; this shim adds the same paths so a bare `pytest` invoked
from the voice-gw directory works too. It mirrors the container layout, where
`backend/` and `voice-gw/` sit side by side on the path.
"""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (_REPO / "backend", _REPO / "voice-gw"):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)
