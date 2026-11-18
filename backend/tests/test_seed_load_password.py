"""seed_load's password: the published one in dev only (it is in this public
repository), the operator's own everywhere else."""

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed_load import LOAD_ORG_SLUG, LOAD_USER_PASSWORD, load_password, seed_load
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import verify_password
from app.modules.tenancy.repository import OrganizationRepository


def test_dev_uses_the_built_in_password() -> None:
    assert load_password([], "dev", None) == LOAD_USER_PASSWORD


def test_staging_refuses_the_built_in_password() -> None:
    with pytest.raises(SystemExit, match="--password-stdin"):
        load_password([], "staging", None)


def test_a_password_on_stdin_is_used_anywhere() -> None:
    assert load_password(["--password-stdin"], "staging", "a-long-enough-password\n") == (
        "a-long-enough-password"
    )


def test_a_short_password_on_stdin_is_refused() -> None:
    with pytest.raises(SystemExit, match="16"):
        load_password(["--password-stdin"], "staging", "short\n")


async def test_seeds_without_a_pro_plan_and_rotates_the_password(
    session: AsyncSession, redis_client: Redis
) -> None:
    """Staging has no synced plans (its Stripe is a placeholder): the seed
    leaves the org on the free plan instead of failing, and a second run with
    another password changes every login account's."""
    await seed_load(session, redis_client, password="first-password-long-enough")
    assert await OrganizationRepository(session).get_by_slug(LOAD_ORG_SLUG) is not None

    await seed_load(session, redis_client, password="second-password-long-enough")

    user = await UserRepository(session).get_by_email("load-user-7@example.com")
    assert user is not None
    assert verify_password("second-password-long-enough", user.hashed_password)
    assert not verify_password("first-password-long-enough", user.hashed_password)
