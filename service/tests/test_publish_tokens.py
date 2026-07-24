"""Publish tokens (plan 006): issue from the API, publish with them, scoping.

Covers: create/get/regenerate/revoke lifecycle, uploading with a publish token,
marketplace scoping (403 on the wrong marketplace), revoked token → 401, JWT
sessions unaffected, and that a publish token cannot mint publish tokens.
"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import init_db
from app.main import app
from app.packaging import package_dir_to_zip

REPO = Path(__file__).resolve().parents[2]
HW2 = REPO / "examples" / "hello_world_2"

EMAIL = "pt-dev@example.com"
PASSWORD = "pw12345"


@pytest.fixture(scope="module", autouse=True)
async def _ready():
    await init_db()
    yield


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(c: AsyncClient) -> dict:
    """Signup (idempotent) + login → auth headers with a session JWT."""
    await c.post("/api/auth/signup", json={
        "email": EMAIL, "username": "ptdev", "password": PASSWORD})
    token = (await c.post("/api/auth/login", json={
        "email": EMAIL, "password": PASSWORD})).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _ensure_marketplace(c: AsyncClient, h: dict, slug: str) -> None:
    await c.post("/api/orgs", json={"name": "PT Co", "slug": "pt-co"}, headers=h)
    await c.post(
        "/api/orgs/pt-co/marketplaces",
        json={"name": slug, "slug": slug, "visibility": "public"},
        headers=h,
    )


async def test_token_lifecycle_and_publish():
    zip_bytes = package_dir_to_zip(HW2)

    async with _client() as c:
        h = await _login(c)
        await _ensure_marketplace(c, h, "pt-mp")
        await _ensure_marketplace(c, h, "pt-mp-other")

        # No token yet
        info = (await c.get("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        assert info["exists"] is False

        # Create
        created = (await c.post("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        secret = created["token"]
        assert secret.startswith("lmp_")
        assert created["token_prefix"] == secret[:12]

        # Metadata now exists, secret never echoed back
        info = (await c.get("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        assert info["exists"] is True
        assert info["token_prefix"] == secret[:12]
        assert "token" not in info

        # Publish with the token instead of the JWT
        pt_h = {"Authorization": f"Bearer {secret}"}
        up = await c.post(
            "/api/marketplaces/pt-mp/upload",
            files={"artifact": ("hw2.zip", zip_bytes, "application/zip")},
            headers=pt_h,
        )
        assert up.status_code == 200, up.text
        assert up.json()["status"] == "published"

        # Scoping: same user owns pt-mp-other, but this token must not work there
        up_other = await c.post(
            "/api/marketplaces/pt-mp-other/upload",
            files={"artifact": ("hw2.zip", zip_bytes, "application/zip")},
            headers=pt_h,
        )
        assert up_other.status_code == 403

        # A publish token cannot mint publish tokens
        mint = await c.post("/api/marketplaces/pt-mp/publish-token", headers=pt_h)
        assert mint.status_code == 403

        # last_used_at recorded
        info = (await c.get("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        assert info["last_used_at"] is not None


async def test_regenerate_invalidates_old_token():
    async with _client() as c:
        h = await _login(c)
        first = (await c.post("/api/marketplaces/pt-mp/publish-token", headers=h)).json()["token"]
        second = (await c.post("/api/marketplaces/pt-mp/publish-token", headers=h)).json()["token"]
        assert first != second

        zip_bytes = package_dir_to_zip(HW2)
        old = await c.post(
            "/api/marketplaces/pt-mp/upload",
            files={"artifact": ("hw2.zip", zip_bytes, "application/zip")},
            headers={"Authorization": f"Bearer {first}"},
        )
        assert old.status_code == 401

        # New token authenticates (409 = duplicate version ⇒ auth passed)
        new = await c.post(
            "/api/marketplaces/pt-mp/upload",
            files={"artifact": ("hw2.zip", zip_bytes, "application/zip")},
            headers={"Authorization": f"Bearer {second}"},
        )
        assert new.status_code == 409


async def test_revoke():
    async with _client() as c:
        h = await _login(c)
        secret = (await c.post("/api/marketplaces/pt-mp/publish-token", headers=h)).json()["token"]

        r = await c.delete("/api/marketplaces/pt-mp/publish-token", headers=h)
        assert r.json()["status"] == "revoked"

        info = (await c.get("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        assert info["exists"] is False

        zip_bytes = package_dir_to_zip(HW2)
        up = await c.post(
            "/api/marketplaces/pt-mp/upload",
            files={"artifact": ("hw2.zip", zip_bytes, "application/zip")},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert up.status_code == 401


async def test_garbage_lmp_token_rejected():
    async with _client() as c:
        r = await c.get("/api/me/marketplaces",
                        headers={"Authorization": "Bearer lmp_not-a-real-token"})
        assert r.status_code == 401


async def test_non_member_cannot_manage_tokens():
    async with _client() as c:
        await c.post("/api/auth/signup", json={
            "email": "stranger@example.com", "username": "stranger", "password": "pw12345"})
        token = (await c.post("/api/auth/login", json={
            "email": "stranger@example.com", "password": "pw12345"})).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        r = await c.post("/api/marketplaces/pt-mp/publish-token", headers=h)
        assert r.status_code == 403
