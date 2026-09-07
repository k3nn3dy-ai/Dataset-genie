"""Every route in the spec's API table must exist (even if 501 until its track lands)."""
from genie.main import create_app

EXPECTED = {
    "/api/health",
    "/api/projects/", "/api/projects/from-preset", "/api/projects/{project_id}",
    "/api/projects/{project_id}/summary",
    "/api/projects/{project_id}/stages/{stage}/estimate", "/api/projects/{project_id}/stages/{stage}/run",
    "/api/projects/{project_id}/taxonomy", "/api/projects/{project_id}/prompts",
    "/api/projects/{project_id}/prompts/resample", "/api/projects/{project_id}/rows",
    "/api/projects/{project_id}/rows/{row_id}", "/api/projects/{project_id}/rows/bulk",
    "/api/projects/{project_id}/refusals", "/api/projects/{project_id}/pairs",
    "/api/projects/{project_id}/judge/summary", "/api/projects/{project_id}/filter/summary",
    "/api/projects/{project_id}/filter/run", "/api/projects/{project_id}/filter/restore",
    "/api/projects/{project_id}/export", "/api/projects/{project_id}/exports",
    "/api/projects/{project_id}/config.yaml",
    "/api/models/", "/api/models/refresh",
    "/api/settings/", "/api/settings/secrets/status", "/api/settings/secrets", "/api/settings/secrets/{name}",
    "/api/runs/{run_id}", "/api/runs/{run_id}/events", "/api/runs/{run_id}/cancel",
    "/api/runs/{run_id}/resume", "/api/runs/{run_id}/log", "/api/presets/",
}


def test_all_spec_routes_exist():
    paths = set(create_app().openapi()["paths"])
    missing = EXPECTED - paths
    assert not missing, f"missing routes: {sorted(missing)}"


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
