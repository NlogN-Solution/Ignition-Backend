"""Reuse the project's own test harness (rolled-back DB session, HTTP client,
user factory, login helper) so these security tests exercise exactly the code
paths the main suite does, against the `ignition_test` database only."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import *  # noqa: E402,F401,F403
