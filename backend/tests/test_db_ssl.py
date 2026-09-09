"""libpq TLS parameters have to survive the trip into asyncpg.

`Settings._enforce_startup_invariants` requires `sslmode=require` (or
stricter) on every Postgres DSN outside dev. SQLAlchemy's asyncpg dialect
builds its connect arguments with a bare `opts.update(url.query)`, so an
`sslmode` left on the URL is handed to `asyncpg.connect()` as a keyword it
does not have, and the connection raises

    TypeError: connect() got an unexpected keyword argument 'sslmode'

before a single byte reaches Postgres -- which made the two requirements
mutually exclusive and every non-dev deployment unbootable. Nothing caught it
because every job that starts the real app runs with ENVIRONMENT=dev, where
neither the invariant nor a TLS DSN applies.

`split_ssl_params` is the bridge. These tests pin both halves of it: the
parameters leave the URL, and they arrive as something asyncpg understands.
"""

import datetime
import ssl
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy.engine.url import make_url

from app.core.config import Settings
from app.core.db import create_engine_for, split_ssl_params

DSN = "postgresql+asyncpg://user:s3cret@db.example.com:5432/forge"


def _write_throwaway_ca(path: Path) -> Path:
    """A self-signed CA on disk, purely so SSLContext has something to load."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forge-test-ca")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return path


def test_sslmode_moves_off_the_url_and_into_connect_args() -> None:
    url, connect_args = split_ssl_params(f"{DSN}?sslmode=require")

    # asyncpg's own `ssl` argument accepts a libpq sslmode string and applies
    # the documented semantics, so it is passed through rather than
    # reinterpreted here.
    assert connect_args == {"ssl": "require"}
    assert "sslmode" not in make_url(url).query


def test_password_survives_the_round_trip() -> None:
    """The regression that hid behind the first fix for this.

    `str(URL)` renders the password as literal `***` -- SQLAlchemy hides it
    in __str__ on purpose. Rebuilding the DSN with it silently swaps the
    credential for three asterisks, and every connection then fails
    authentication instead of failing TLS, which sends you looking in
    entirely the wrong place.
    """
    url, _ = split_ssl_params(f"{DSN}?sslmode=require")

    assert make_url(url).password == "s3cret"
    assert "***" not in url


def test_dsn_without_ssl_params_is_returned_untouched() -> None:
    """The dev path pays nothing: same string, no connect args."""
    url, connect_args = split_ssl_params(DSN)

    assert url == DSN
    assert connect_args == {}


def test_other_query_parameters_are_preserved() -> None:
    url, connect_args = split_ssl_params(f"{DSN}?sslmode=require&application_name=forge")

    assert connect_args == {"ssl": "require"}
    assert make_url(url).query["application_name"] == "forge"


@pytest.mark.parametrize(
    ("sslmode", "check_hostname"),
    [("verify-full", True), ("verify-ca", False)],
)
def test_sslrootcert_becomes_a_real_ssl_context(
    sslmode: str, check_hostname: bool, tmp_path: Path
) -> None:
    """asyncpg only reads `sslrootcert` when it parses a DSN itself.

    Under SQLAlchemy it never gets to, so a CA bundle named on the URL would
    be dropped on the floor and a `verify-full` deployment would quietly fall
    back to asyncpg's default trust store. It has to be turned into an
    SSLContext here instead.

    The CA is generated rather than borrowed from the platform:
    `ssl.get_default_verify_paths().cafile` is None on Windows, where trust
    lives in the system store and not in a file anyone can point at.
    """
    ca = _write_throwaway_ca(tmp_path / "ca.pem")

    url, connect_args = split_ssl_params(f"{DSN}?sslmode={sslmode}&sslrootcert={ca}")

    context = connect_args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is check_hostname
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert make_url(url).query == {}


def test_engine_is_built_with_the_translated_arguments() -> None:
    """The end of the chain: what create_async_engine is actually given.

    This is the assertion that would have failed on the shipped code -- the
    engine was handed a URL still carrying `sslmode`, which only became a
    TypeError on first connect, i.e. at deploy time rather than at test time.
    """
    settings = Settings()
    engine = create_engine_for(f"{DSN}?sslmode=require", settings)

    assert "sslmode" not in engine.url.query
    assert engine.dialect.create_connect_args(engine.url)[1].get("sslmode") is None
