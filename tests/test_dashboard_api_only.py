"""Standalone backend image is not required to embed a UI release artifact."""
from pathlib import Path

from fastapi import FastAPI

import dashboard


def test_api_only_deployment_without_frontend_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "build_dir", tmp_path / "missing")
    monkeypatch.setattr(dashboard, "statics_dir", tmp_path / "missing" / "statics")
    app = FastAPI()
    dashboard.run_build(app)
    assert not any(getattr(route, "name", "") == "dashboard" for route in app.routes)


def test_combined_deployment_keeps_legacy_dashboard(monkeypatch, tmp_path):
    build = tmp_path / "web"
    statics = build / "statics"
    statics.mkdir(parents=True)
    (build / "index.html").write_text("<html>ok</html>")
    monkeypatch.setattr(dashboard, "build_dir", build)
    monkeypatch.setattr(dashboard, "statics_dir", statics)
    app = FastAPI()
    dashboard.run_build(app)
    assert any(getattr(route, "name", "") == "dashboard" for route in app.routes)
    assert any(getattr(route, "name", "") == "statics" for route in app.routes)
