"""Offline checks in the spirit of hassfest (which is not shipped on PyPI)."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from awesomeversion import AwesomeVersion
import pytest
import yaml

from custom_components.boxblip.const import DOMAIN, STATUSES
from custom_components.boxblip.sensor import PARCEL_SENSORS, SUMMARY_SENSORS
from homeassistant.helpers import selector

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / DOMAIN


def _load(name: str) -> Any:
    return json.loads((INTEGRATION / name).read_text(encoding="utf-8"))


def _keys(data: Any, prefix: str = "") -> set[str]:
    if not isinstance(data, dict):
        return {prefix}
    out: set[str] = set()
    for key, value in data.items():
        out |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return out


def _placeholders(data: Any) -> dict[str, set[str]]:
    flat: dict[str, set[str]] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        else:
            flat[path] = set(re.findall(r"\{(\w+)\}", node))

    walk(data, "")
    return flat


def test_manifest() -> None:
    """Required keys, ordering and values."""
    manifest = _load("manifest.json")
    for key in (
        "domain",
        "name",
        "version",
        "documentation",
        "issue_tracker",
        "codeowners",
        "config_flow",
        "iot_class",
        "integration_type",
        "requirements",
    ):
        assert key in manifest, key
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    assert manifest["domain"] == DOMAIN
    assert AwesomeVersion(manifest["version"]).valid
    assert manifest["documentation"].startswith("https://")
    assert manifest["issue_tracker"].startswith("https://")
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] in {
        "assumed_state",
        "calculated",
        "cloud_polling",
        "cloud_push",
        "local_polling",
        "local_push",
    }
    assert manifest["integration_type"] in {"device", "hub", "service"}
    assert manifest["requirements"] == []
    assert "webhook" in manifest["dependencies"]


def test_hacs_json() -> None:
    """hacs.json has the required name and a parseable minimum version."""
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"]
    assert AwesomeVersion(hacs["homeassistant"]).valid
    assert set(hacs) <= {
        "name",
        "content_in_root",
        "zip_release",
        "filename",
        "hide_default_branch",
        "country",
        "homeassistant",
        "hacs",
        "persistent_directory",
    }


def test_brand_icon_present() -> None:
    """Custom integrations ship their own icon since 2026.3."""
    assert (INTEGRATION / "brand" / "icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_strings_equal_en() -> None:
    """strings.json is the English source and identical to translations/en.json."""
    assert _load("strings.json") == _load("translations/en.json")


@pytest.mark.parametrize("language", ["it"])
def test_translations_complete(language: str) -> None:
    """Every translation has exactly the English keys and placeholders."""
    english = _load("strings.json")
    other = _load(f"translations/{language}.json")
    assert _keys(other) == _keys(english)
    assert _placeholders(other) == _placeholders(english)


def test_translations_cover_entities() -> None:
    """Every translation_key and enum state used in code is translated."""
    strings = _load("strings.json")
    sensors = strings["entity"]["sensor"]
    assert set(sensors["parcel"]["state"]) == set(STATUSES)
    for description in (*SUMMARY_SENSORS, *PARCEL_SENSORS):
        assert description.translation_key in sensors
    for key in ("next_delivery", "last_read"):
        assert key in sensors
    assert "delivery_today" in strings["entity"]["binary_sensor"]
    assert "deliveries" in strings["entity"]["calendar"]
    for key in (
        "auth_failed",
        "cannot_connect",
        "entry_not_found",
        "entry_not_loaded",
        "rate_limited",
        "server_disabled",
    ):
        assert key in strings["exceptions"]


def test_icons_cover_translations() -> None:
    """icons.json uses the same keys as the entity translations."""
    icons = _load("icons.json")
    strings = _load("strings.json")
    assert set(icons["entity"]) == set(strings["entity"])
    for platform in ("sensor", "binary_sensor", "calendar"):
        assert set(icons["entity"][platform]) == set(strings["entity"][platform])
    assert set(icons["entity"]["sensor"]["parcel"]["state"]) == set(STATUSES)
    assert set(icons["services"]) == set(strings["services"])


def test_services_yaml_matches_translations() -> None:
    """Every action and field in services.yaml is translated, and selectors are valid."""
    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text())
    strings = _load("strings.json")["services"]
    assert set(services) == set(strings)
    for name, service in services.items():
        assert "name" in strings[name]
        assert "description" in strings[name]
        fields = service.get("fields") or {}
        assert set(fields) == set(strings[name].get("fields", {}))
        for field in fields.values():
            selector.validate_selector(field["selector"])


def test_config_flow_strings_cover_steps() -> None:
    """Every step, field and error used by the flows has a string."""
    config = _load("strings.json")["config"]
    assert set(config["step"]) == {"user", "reauth_confirm", "reconfigure"}
    for step in config["step"].values():
        assert set(step["data"]) <= set(step["data_description"]) | {"url"}
    assert set(config["error"]) == {
        "cannot_connect",
        "invalid_auth",
        "invalid_token_format",
        "invalid_url",
        "server_disabled",
        "unknown",
    }
    assert {"already_configured", "reauth_successful", "reconfigure_successful"} <= set(
        config["abort"]
    )
