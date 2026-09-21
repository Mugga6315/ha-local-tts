"""Make the integration's HA-free modules importable in tests.

The package's __init__.py imports Home Assistant, so importing it as a package
would need a full HA install. audio.py deliberately has no HA imports, so its
directory goes on sys.path and tests import it as a plain module."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "local_tts"))
