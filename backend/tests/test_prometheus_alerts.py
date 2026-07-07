"""T-205: validates docs/grafana/alerts.yml with the real `promtool` binary
(shipped inside the official prom/prometheus image, the same tag
docker-compose.yml's observability profile already pins) rather than a
hand-rolled YAML shape check -- `promtool check rules` catches PromQL syntax
errors and rule-file schema violations plain YAML parsing can't. Same
"validate infra config from a plain pytest file, no separate CI script"
pattern as test_caddy_config.py / test_grafana_dashboards.py.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
GRAFANA_DIR = REPO_ROOT / "docs" / "grafana"
ALERTS_FILE = GRAFANA_DIR / "alerts.yml"
PROMETHEUS_IMAGE = "prom/prometheus:v2.55.1"


def test_alerts_file_exists_and_is_non_empty() -> None:
    assert ALERTS_FILE.exists()
    assert ALERTS_FILE.read_text().strip()


def test_promtool_check_rules_passes() -> None:
    result = subprocess.run(  # noqa: S603 -- fixed args, no untrusted input
        [  # noqa: S607 -- "docker" resolved via PATH like every other docker invocation a dev/CI runner already trusts
            "docker",
            "run",
            "--rm",
            # prom/prometheus's image ENTRYPOINT is /bin/prometheus itself --
            # without this override, "promtool" would be appended as an
            # argument to that binary instead of replacing it.
            "--entrypoint=/bin/promtool",
            "-v",
            f"{GRAFANA_DIR}:/rules:ro",
            PROMETHEUS_IMAGE,
            "check",
            "rules",
            "/rules/alerts.yml",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
