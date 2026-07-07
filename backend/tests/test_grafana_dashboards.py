"""
Verifies T-204: every docs/grafana/*.json dashboard is valid, portable JSON
-- parses, selects its datasource through the DS_PROMETHEUS template
variable, and never pins a panel to one Grafana instance's datasource uid.
"""

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
GRAFANA_DIR = REPO_ROOT / "docs" / "grafana"
DASHBOARDS_DIR = GRAFANA_DIR / "dashboards"
DASHBOARD_FILES = ["api-overview.json", "workers.json", "database.json", "business.json"]


def _load(filename: str) -> dict[str, Any]:
    return json.loads((DASHBOARDS_DIR / filename).read_text())  # type: ignore[no-any-return]


def _iter_datasource_uids(node: object) -> list[str]:
    uids: list[str] = []
    if isinstance(node, dict):
        datasource = node.get("datasource")
        if isinstance(datasource, dict) and "uid" in datasource:
            uids.append(datasource["uid"])
        for value in node.values():
            uids.extend(_iter_datasource_uids(value))
    elif isinstance(node, list):
        for item in node:
            uids.extend(_iter_datasource_uids(item))
    return uids


@pytest.mark.parametrize("filename", DASHBOARD_FILES)
def test_dashboard_json_parses(filename: str) -> None:
    dashboard = _load(filename)
    assert dashboard["panels"]


@pytest.mark.parametrize("filename", DASHBOARD_FILES)
def test_dashboard_declares_ds_prometheus_variable(filename: str) -> None:
    dashboard = _load(filename)
    variables = {v["name"]: v for v in dashboard["templating"]["list"]}
    assert "DS_PROMETHEUS" in variables
    assert variables["DS_PROMETHEUS"]["type"] == "datasource"
    assert variables["DS_PROMETHEUS"]["query"] == "prometheus"


@pytest.mark.parametrize("filename", DASHBOARD_FILES)
def test_dashboard_has_no_hardcoded_datasource_uid(filename: str) -> None:
    dashboard = _load(filename)
    uids = _iter_datasource_uids(dashboard["panels"])
    assert uids, "expected at least one panel-level datasource reference"
    assert all(uid == "${DS_PROMETHEUS}" for uid in uids)


@pytest.mark.parametrize("filename", DASHBOARD_FILES)
def test_dashboard_id_is_null_and_uid_is_stable(filename: str) -> None:
    dashboard = _load(filename)
    assert dashboard["id"] is None
    assert dashboard["uid"].startswith("forge-")


def test_prometheus_scrape_config_targets_backend_metrics() -> None:
    content = (GRAFANA_DIR / "prometheus.yml").read_text()
    assert "backend:8000" in content
    assert "/metrics" in content


def test_grafana_provisioning_wires_prometheus_datasource() -> None:
    content = (GRAFANA_DIR / "provisioning" / "datasources" / "datasource.yml").read_text()
    assert "type: prometheus" in content


def test_grafana_provisioning_wires_dashboard_provider() -> None:
    content = (GRAFANA_DIR / "provisioning" / "dashboards" / "dashboards.yml").read_text()
    assert "/etc/grafana/dashboards-json" in content
