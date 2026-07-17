import httpx

from teampulse.config import Settings, get_settings
from teampulse.main import create_app


async def test_api_key_is_optional_when_not_configured():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/source-items/ingest", json={})

    assert response.status_code != 401


async def test_api_key_blocks_protected_routes_when_configured():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post("/api/v1/source-items/ingest", json={})
        wrong = await client.post(
            "/api/v1/source-items/ingest",
            json={},
            headers={"X-TeamPulse-API-Key": "wrong"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_health_route_does_not_require_api_key():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
