"""
Verifies the Alembic setup itself (T-010), not any feature migration:
  - env.py loads the DB URL from Settings, not from alembic.ini (no ini file
    is ever given to the Config built below — if env.py fell back to reading
    "sqlalchemy.url" instead of calling get_settings(), these tests would
    fail to connect).
  - autogenerate, against the real async env.py, produces a working revision
    for a model built from the shared mixins.
  - upgrade -> downgrade -> upgrade round-trips cleanly.

Each test gets its own Postgres container and its own copy of the real
alembic/env.py + script.py.mako in a tmp dir, so a generated revision file
never lands in the repo's actual alembic/versions/ and no alembic_version
stamp leaks between tests.
"""

import shutil
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlmodel import Field, SQLModel
from testcontainers.postgres import PostgresContainer

from app.core.config import get_settings
from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin

_REAL_ALEMBIC_DIR = Path(__file__).parent.parent / "alembic"


class AlembicRoundTripEntity(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "alembic_round_trip_entity"
    __test__: ClassVar[bool] = False

    org_id: UUID = Field(index=True)
    name: str


@pytest.fixture
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture
def alembic_config(
    tmp_path: Path, postgres_container: "PostgresContainer", monkeypatch: pytest.MonkeyPatch
) -> Config:
    alembic_dir = tmp_path / "alembic"
    (alembic_dir / "versions").mkdir(parents=True)
    shutil.copy(_REAL_ALEMBIC_DIR / "env.py", alembic_dir / "env.py")
    shutil.copy(_REAL_ALEMBIC_DIR / "script.py.mako", alembic_dir / "script.py.mako")

    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()

    # No ini file passed in: proves env.py sources the DB URL from
    # get_settings() rather than from alembic.ini.
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    return config


def _versions_dir(config: Config) -> Path:
    return Path(config.get_main_option("script_location")) / "versions"


def test_autogenerate_produces_valid_sql_for_mixin_columns(alembic_config: Config) -> None:
    command.revision(alembic_config, message="mixin columns", autogenerate=True)

    generated = list(_versions_dir(alembic_config).glob("*.py"))
    assert len(generated) == 1
    script = generated[0].read_text()

    assert "alembic_round_trip_entity" in script
    assert "created_at" in script
    assert "updated_at" in script
    assert "deleted_at" in script
    assert "org_id" in script
    assert "def downgrade" in script


def test_upgrade_downgrade_upgrade_round_trip(alembic_config: Config) -> None:
    command.revision(alembic_config, message="mixin columns", autogenerate=True)

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_script_template_reminds_authors_to_write_downgrade() -> None:
    template = (_REAL_ALEMBIC_DIR / "script.py.mako").read_text()
    assert "downgrade" in template.lower()
    assert "def downgrade" in template
