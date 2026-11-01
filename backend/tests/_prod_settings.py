"""Everything a non-dev ENVIRONMENT demands, in one place.

A test that asserts one startup invariant starts from this and breaks exactly
the setting it is about; without it, the unrelated invariants fire first.
Every new non-dev invariant adds its passing value here, once.
"""

PROD_SAFE_OVERRIDES: dict[str, object] = {
    "ENVIRONMENT": "prod",
    "FORCE_HTTPS": True,
    "DATA_SENSITIVITY": "standard",
    "VIRUS_SCANNER": "clamav",
    "PASSWORD_PEPPER": "a-real-pepper",
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db?sslmode=require",
    "REDIS_URL": "rediss://localhost:6379/0",
    # The compose posture: Caddy answers /metrics with a 404 before the app
    # ever sees the request (verified for both Caddyfiles by
    # test_caddy_config.py). A deployment with no such edge -- Render, Fly,
    # Cloud Run -- sets METRICS_TOKEN instead; one of the two is mandatory.
    "METRICS_BLOCKED_AT_EDGE": True,
    # Full-cost Argon2: the fixture env file is .env.test, which lowers it
    # for speed, and production may not.
    "ARGON2_TIME_COST": 3,
    "ARGON2_MEMORY_KIB": 65536,
    "ARGON2_PARALLELISM": 4,
}
