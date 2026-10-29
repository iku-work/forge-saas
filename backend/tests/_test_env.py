"""backend/.env.test plus key material generated for this test run.

.env.test carries no keys (see scripts/throwaway_keys.py). Each run generates
them, exports them, and writes TEST_ENV_FILE -- .env.test with the keys
appended -- for the tests that build Settings from a file.

Imported by tests/__init__.py, so it runs before conftest imports anything
from `app`: some modules build Settings at import time.
"""

import atexit
import os
import tempfile
from pathlib import Path

from scripts.throwaway_keys import generate

KEYS = generate()
os.environ.update(KEYS)

_fixture = (Path(__file__).parent.parent / ".env.test").read_text(encoding="utf-8")
_fd, _path = tempfile.mkstemp(prefix="forge-env-test-", suffix=".env")
with os.fdopen(_fd, "w", encoding="utf-8") as _f:
    _f.write(_fixture.rstrip("\n") + "\n")
    _f.writelines(f'{name}="{value}"\n' for name, value in KEYS.items())
TEST_ENV_FILE = Path(_path)
atexit.register(TEST_ENV_FILE.unlink, missing_ok=True)
