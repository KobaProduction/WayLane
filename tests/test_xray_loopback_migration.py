"""The consolidated baseline must not ship an exposed Xray client listener."""

import json
from pathlib import Path


def test_baseline_xray_template_binds_client_inbounds_to_loopback():
    path = Path("app/db/migrations/bootstrap_seed.json")
    defaults = json.loads(path.read_text())
    templates = [
        item for item in defaults["client_templates"]
        if item["template_type"] == "xray_subscription" and item["is_system"]
    ]
    assert templates, "Initial schema must include the default Xray subscription template"
    for item in templates:
        data = json.loads(item["content"])
        for inbound in data.get("inbounds", []):
            assert inbound.get("listen", "127.0.0.1") in {"127.0.0.1", "::1"}
