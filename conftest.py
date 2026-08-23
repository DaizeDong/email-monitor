"""Keep the test suite out of the operator's real log and state.

`em_tick.log()` writes to `$EMAIL_MONITOR_LOG`, default
`~/.local/state/email-monitor/email-monitor.log`. Nothing in the suite used to
override it, so every `pytest` run appended its synthetic failures to the file a
human reads to find real ones. Measured on 2026-08-23: 58 lines of
`classify failed (boom) -> heuristic` and 24 of `topic label MATCHED ...
(not applied)` in four days, all from fixtures, none from production.

That is not cosmetic. The point of logging `failed` separately from `unsure` is
so an operator can grep for the calls that actually broke; a log salted with
fixture failures makes a real one indistinguishable from test noise by exactly
the search you would run to find it.

WHY THIS IS MODULE LEVEL AND NOT A FIXTURE: `em_tick.LOG` is resolved once, at
import time. pytest imports test modules (and therefore `em_tick`) BEFORE any
fixture runs, so an autouse fixture setting the variable would run too late and
change nothing, while looking exactly like a fix. conftest.py is imported before
collection, which is the only window where this assignment still lands.

Verify by running the suite and checking the real log did not grow.
"""
import os
import tempfile

_SANDBOX = os.path.join(tempfile.gettempdir(), "email-monitor-tests")
os.makedirs(os.path.join(_SANDBOX, "state"), exist_ok=True)

# setdefault, not assignment: a caller who deliberately points these somewhere
# (a debugging run, CI collecting artifacts) keeps their choice.
os.environ.setdefault("EMAIL_MONITOR_LOG",
                      os.path.join(_SANDBOX, "email-monitor.log"))
os.environ.setdefault("EMAIL_MONITOR_STATE_DIR",
                      os.path.join(_SANDBOX, "state"))
