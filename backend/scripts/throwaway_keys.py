"""Throwaway JWT keypair and Fernet key, for anything that needs a valid
Settings but no real secrets: the test suite (tests/_test_env.py) and the
schema dump (scripts/dump_openapi.py).

The committed env fixtures carry no keys of their own -- a key committed to a
public repo is a published key, and production refuses the ones that once
were (app.core.config). scripts/gen-test-secrets.mjs is the same thing for the
CI jobs and Makefile targets that only have a shell.
"""

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate() -> dict[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "JWT_PRIVATE_KEY": private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
        .strip(),
        "JWT_PUBLIC_KEY": private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
        .strip(),
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
