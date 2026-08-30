"""Session-wide sandbox: the suite must not touch the operator's audit trail."""

import atexit as _atexit
import os as _os
import shutil as _shutil
import tempfile as _tempfile
from pathlib import Path as _Path

from vmware_policy.audit import reset_engine as _reset_engine

# ── shared-audit sandbox, installed at import time ───────────────────────────
#
# A session-scoped autouse fixture is too late for this one: vmware_policy's
# audit engine is a lazily built singleton keyed to the path it first resolved,
# and per-skill loggers bind Path.home() when their module is imported —
# collection has already imported every test module before any fixture runs.
#
# OPS_HOME moves vmware_policy's shared ~/.vmware/audit.db. Without it this
# suite appended 26 rows per run to the operator's real audit trail, which
# held over 30,000 — most of them tool names nobody had ever invoked. An audit
# trail containing test fiction cannot answer the question it is kept for.
_REAL_HOME = _Path(_os.path.expanduser("~"))
_SANDBOX_HOME = _Path(_tempfile.mkdtemp(prefix="vmware-pilot-tests-"))
_os.environ["HOME"] = str(_SANDBOX_HOME)
_os.environ["OPS_HOME"] = str(_SANDBOX_HOME / ".vmware")
_os.environ["USERPROFILE"] = str(_SANDBOX_HOME)
_reset_engine()
_atexit.register(_shutil.rmtree, _SANDBOX_HOME, True)
