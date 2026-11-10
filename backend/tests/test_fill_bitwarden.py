"""scripts/fill-bitwarden.sh, run for real with `bws` and `ssh` replaced by
scripts that record what they were asked to do. openssl is the real one."""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT = "3f6ad220-9191-4159-a745-b4fb011f148a"
BOX_VALUES = """\
DATABASE_URL=postgresql+asyncpg://forge_app:pw@pgbouncer:6432/forge?sslmode=require
REDIS_URL=rediss://:pw@redis:6379/0?ssl_ca_certs=/certs/ca.crt
AWS_ACCESS_KEY_ID=forge-app
AWS_SECRET_ACCESS_KEY=abc=def

# Not for the Bitwarden deploy project: back this up somewhere off the box.
MINIO_KMS_SECRET_KEY=forge:kms
"""


def _fill(
    workspace: Path,
    answers: str,
    *,
    existing: tuple[str, ...] = (),
    box_values: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str], list[str]]:
    """Run the script; return its result, what it created, and ssh's calls."""
    bash, openssl = shutil.which("bash"), shutil.which("openssl")
    if bash is None or openssl is None:
        pytest.skip("needs bash and openssl")
    shutil.copytree(REPO_ROOT / "deploy", workspace / "deploy")
    (workspace / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "fill-bitwarden.sh", workspace / "scripts")
    listed = [{"key": name, "value": "x", "projectId": PROJECT} for name in existing]
    (workspace / "listed.json").write_text(json.dumps(listed, indent=2), encoding="utf-8")
    (workspace / "box.txt").write_text(box_values or "", encoding="utf-8")
    bin_dir = workspace / "bin"
    bin_dir.mkdir()
    fakes = {
        # `secret list <project> --output json`, `secret create NAME VALUE PROJECT ...`
        "bws": f"""#!/bin/bash
if [ "$2" = list ]; then [ "$3" = {PROJECT} ] && cat listed.json; exit; fi
printf '%s %s %s\\n' "$3" "$5" "$(printf %s "$4" | base64 -w0)" >> created.txt
""",
        "ssh": """#!/bin/bash
echo "$*" >> ssh.txt
[ "$2" = cat ] && cat box.txt
exit 0
""",
    }
    for name, text in fakes.items():
        (bin_dir / name).write_text(text, encoding="utf-8", newline="\n")
        (bin_dir / name).chmod(0o755)
    args = ["staging"] + (["box-root"] if box_values is not None else [])
    result = subprocess.run(  # noqa: S603 -- fixed argv: the script under test
        [bash, "scripts/fill-bitwarden.sh", *args],
        cwd=workspace,
        input=answers,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "BWS_ACCESS_TOKEN": "token",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    created: dict[str, str] = {}
    created_file = workspace / "created.txt"
    if created_file.exists():
        for line in created_file.read_text(encoding="utf-8").splitlines():
            name, project, value = line.split(" ")
            assert project == PROJECT
            created[name] = base64.b64decode(value).decode()
    ssh_file = workspace / "ssh.txt"
    ssh = ssh_file.read_text(encoding="utf-8").splitlines() if ssh_file.exists() else []
    return result, created, ssh


def test_a_new_project_gets_working_keys_and_checked_third_party_secrets(
    tmp_path: Path,
) -> None:
    # The webhook secret is pasted wrong once, then right; everything after it
    # is skipped with Enter.
    answers = "sk_test_abc123\nnot-a-secret\nwhsec_xyz\n\n\n\n\n\n"
    result, created, _ = _fill(tmp_path, answers)

    assert result.returncode == 0, result.stderr
    private = serialization.load_pem_private_key(created["JWT_PRIVATE_KEY"].encode(), None)
    public = serialization.load_pem_public_key(created["JWT_PUBLIC_KEY"].encode())
    assert isinstance(private, rsa.RSAPrivateKey)
    spki = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert private.public_key().public_bytes(*spki) == public.public_bytes(*spki)
    Fernet(created["ENCRYPTION_KEY"].encode())
    for name in ("URL_TOKEN_SECRET", "PASSWORD_PEPPER", "METRICS_TOKEN"):
        assert len(created[name]) == 64
    assert len({created[n] for n in ("URL_TOKEN_SECRET", "PASSWORD_PEPPER", "METRICS_TOKEN")}) == 3
    assert created["STRIPE_SECRET_KEY"] == "sk_test_abc123"
    assert created["STRIPE_WEBHOOK_SECRET"] == "whsec_xyz"
    assert "RESEND_API_KEY" not in created
    assert "That does not look like STRIPE_WEBHOOK_SECRET" in result.stderr
    # Enter skips; it is not a wrong answer.
    assert result.stderr.count("That does not look like") == 1
    assert "Still not in Bitwarden: DATABASE_URL REDIS_URL" in result.stdout
    # Nothing it set is ever shown.
    for value in created.values():
        assert value not in result.stdout
        assert value not in result.stderr


def test_a_second_run_never_replaces_what_is_there(tmp_path: Path) -> None:
    existing = ("JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY", "ENCRYPTION_KEY", "STRIPE_SECRET_KEY")
    result, created, _ = _fill(tmp_path, "\n" * 6, existing=existing)

    assert result.returncode == 0, result.stderr
    assert set(created) == {"URL_TOKEN_SECRET", "PASSWORD_PEPPER", "METRICS_TOKEN"}
    assert "left as they are: JWT_PRIVATE_KEY JWT_PUBLIC_KEY ENCRYPTION_KEY" in result.stdout


def test_half_a_jwt_pair_stops_the_run(tmp_path: Path) -> None:
    result, created, _ = _fill(tmp_path, "", existing=("JWT_PUBLIC_KEY",))

    assert result.returncode != 0
    assert "Only one of JWT_PRIVATE_KEY and JWT_PUBLIC_KEY" in result.stderr
    assert created == {}


def test_the_box_values_go_in_and_the_file_goes_away(tmp_path: Path) -> None:
    result, created, ssh = _fill(
        tmp_path, "y\n" + "\n" * 7, existing=("REDIS_URL",), box_values=BOX_VALUES
    )

    assert result.returncode == 0, result.stderr
    assert created["DATABASE_URL"].startswith("postgresql+asyncpg://forge_app:pw@pgbouncer")
    assert created["AWS_SECRET_ACCESS_KEY"] == "abc=def"
    assert created["AWS_ACCESS_KEY_ID"] == "forge-app"
    # It decrypts every uploaded file; the deploy must never be able to read it.
    assert "MINIO_KMS_SECRET_KEY" not in created
    assert "skipped MINIO_KMS_SECRET_KEY" in result.stdout
    # The old server's password, kept, would leave the app unable to connect.
    assert "REDIS_URL" not in created
    assert "Already in Bitwarden, not replaced: REDIS_URL" in result.stderr
    assert ssh == [
        "box-root cat /home/forge/forge/bitwarden-values.txt",
        "box-root rm /home/forge/forge/bitwarden-values.txt",
    ]


def test_the_box_file_stays_unless_the_answer_is_yes(tmp_path: Path) -> None:
    result, _, ssh = _fill(tmp_path, "\n" * 8, box_values=BOX_VALUES)

    assert result.returncode == 0, result.stderr
    assert ssh == ["box-root cat /home/forge/forge/bitwarden-values.txt"]
