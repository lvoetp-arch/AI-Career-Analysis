import sys
import textwrap

from fastapi.testclient import TestClient


def test_router_autodiscovery(make_app, tmp_path, monkeypatch):
    pkg = tmp_path / "fakerouters"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hello.py").write_text(textwrap.dedent("""
        from fastapi import APIRouter
        router = APIRouter()
        @router.get("/api/hello")
        def hello():
            return {"hi": 1}
    """))
    (pkg / "_private.py").write_text("raise RuntimeError('should not import')")
    monkeypatch.syspath_prepend(str(tmp_path))
    import fakerouters

    from app.main import include_discovered_routers

    app = make_app()
    include_discovered_routers(app, fakerouters)
    assert TestClient(app).get("/api/hello").json() == {"hi": 1}
    sys.modules.pop("fakerouters", None)


def test_static_frontend_mounted_and_api_still_works(settings, tmp_path, monkeypatch):
    import app.main as main

    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    monkeypatch.setattr(main, "FRONTEND_DIR", tmp_path)
    app = main.create_app(settings)
    c = TestClient(app)
    assert "hi" in c.get("/").text
    assert c.get("/api/health").json() == {"status": "ok"}
    r = c.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_cors_only_for_allowed_origins(make_app, settings):
    from dataclasses import replace

    app = make_app(settings_override=replace(settings, allowed_origins=("http://localhost:5500",)))
    c = TestClient(app)
    ok = c.get("/api/health", headers={"Origin": "http://localhost:5500"})
    bad = c.get("/api/health", headers={"Origin": "http://evil.example.org"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5500"
    assert "access-control-allow-origin" not in bad.headers
