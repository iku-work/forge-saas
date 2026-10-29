"""backend/.env.test plus key material generated for this test run.

.env.test carries no keys: a key committed to a public repo is a published
key, and production refuses the ones that once were (app.core.config). Each
run generates a JWT keypair and a Fernet key, exports them, and writes
TEST_ENV_FILE -- .env.test with the keys appended -- for the tests that build
Settings from a file.

Imported by tests/__init__.py, so it runs before conftest imports anything
from `app`: some modules build Settings at import time.
"""

import atexit
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEYS = {
    "JWT_PRIVATE_KEY": _private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode()
    .strip(),
    "JWT_PUBLIC_KEY": _private.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
    .strip(),
    "ENCRYPTION_KEY": Fernet.generate_key().decode(),
}
os.environ.update(KEYS)

_fixture = (Path(__file__).parent.parent / ".env.test").read_text(encoding="utf-8")
_fd, _path = tempfile.mkstemp(prefix="forge-env-test-", suffix=".env")
with os.fdopen(_fd, "w", encoding="utf-8") as _f:
    _f.write(_fixture.rstrip("\n") + "\n")
    _f.writelines(f'{name}="{value}"\n' for name, value in KEYS.items())
TEST_ENV_FILE = Path(_path)
atexit.register(TEST_ENV_FILE.unlink, missing_ok=True)
