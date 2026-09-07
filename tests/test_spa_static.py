"""The production SPA catch-all must never serve files outside frontend/dist."""
from fastapi.testclient import TestClient


def _dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>Dataset Genie</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    return dist


def test_spa_blocks_path_traversal(genie_home, tmp_path, monkeypatch):
    from genie import main

    monkeypatch.setattr(main, "FRONTEND_DIST", _dist(tmp_path))
    with TestClient(main.create_app()) as c:
        for path in ["/%2e%2e/secret.txt", "/..%2Fsecret.txt", "/../secret.txt", "/assets/../../secret.txt"]:
            r = c.get(path)
            assert "TOP-SECRET" not in r.text, path
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert "Dataset Genie" in c.get("/p/abc/3").text  # SPA fallback
