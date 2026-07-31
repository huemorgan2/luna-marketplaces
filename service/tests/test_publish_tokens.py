"""Publish tokens (plan 006): issue from the API, publish with them, scoping.

Covers: create/list/revoke lifecycle, multiple named tokens coexisting,
uploading with a publish token, marketplace scoping (403 on the wrong
marketplace), revoked token → 401, JWT sessions unaffected, and that a
publish token cannot mint publish tokens.
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

        # No tokens yet
        info = (await c.get("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        assert info["tokens"] == []

        # Create a named token
        created = (await c.post(
            "/api/marketplaces/pt-mp/publish-token",
            json={"name": "CI deploy"}, headers=h)).json()
        secret = created["token"]
        assert secret.startswith("lmp_")
        assert created["token_prefix"] == secret[:12]
        assert created["name"] == "CI deploy"
        assert created["created_at"] is not None

        # Metadata now exists, secret never echoed back
        info = (await c.get("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        assert len(info["tokens"]) == 1
        item = info["tokens"][0]
        assert item["name"] == "CI deploy"
        assert item["token_prefix"] == secret[:12]
        assert item["created_at"] == created["created_at"]
        assert "token" not in item

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
        assert info["tokens"][0]["last_used_at"] is not None


async def test_multiple_tokens_coexist():
    async with _client() as c:
        h = await _login(c)
        first = (await c.post(
            "/api/marketplaces/pt-mp/publish-token",
            json={"name": "laptop"}, headers=h)).json()
        second = (await c.post(
            "/api/marketplaces/pt-mp/publish-token",
            json={"name": "build server"}, headers=h)).json()
        assert first["token"] != second["token"]

        # Both listed, each with its own name and created date
        info = (await c.get("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        names = {t["name"] for t in info["tokens"]}
        assert {"laptop", "build server"} <= names
        assert all(t["created_at"] is not None for t in info["tokens"])

        # Both authenticate (409 = duplicate version ⇒ auth passed)
        zip_bytes = package_dir_to_zip(HW2)
        for tok in (first["token"], second["token"]):
            r = await c.post(
                "/api/marketplaces/pt-mp/upload",
                files={"artifact": ("hw2.zip", zip_bytes, "application/zip")},
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert r.status_code in (200, 409), r.text


async def test_create_without_name_defaults_empty():
    async with _client() as c:
        h = await _login(c)
        created = (await c.post("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        assert created["name"] == ""
        assert created["token"].startswith("lmp_")


async def test_revoke_single_token():
    async with _client() as c:
        h = await _login(c)
        kept = (await c.post(
            "/api/marketplaces/pt-mp/publish-token",
            json={"name": "kept"}, headers=h)).json()
        doomed = (await c.post(
            "/api/marketplaces/pt-mp/publish-token",
            json={"name": "doomed"}, headers=h)).json()

        r = await c.delete(
            f"/api/marketplaces/pt-mp/publish-token/{doomed['id']}", headers=h)
        assert r.json()["status"] == "revoked"

        # Only the revoked token disappears from the list
        info = (await c.get("/api/marketplaces/pt-mp/publish-token", headers=h)).json()
        ids = {t["id"] for t in info["tokens"]}
        assert kept["id"] in ids
        assert doomed["id"] not in ids

        # Revoked token → 401; the other still authenticates
        zip_bytes = package_dir_to_zip(HW2)
        dead = await c.post(
            "/api/marketplaces/pt-mp/upload",
            files={"artifact": ("hw2.zip", zip_bytes, "application/zip")},
            headers={"Authorization": f"Bearer {doomed['token']}"},
        )
        assert dead.status_code == 401
        alive = await c.post(
            "/api/marketplaces/pt-mp/upload",
            files={"artifact": ("hw2.zip", zip_bytes, "application/zip")},
            headers={"Authorization": f"Bearer {kept['token']}"},
        )
        assert alive.status_code in (200, 409)

        # Revoking an already-revoked or unknown token id → 404
        again = await c.delete(
            f"/api/marketplaces/pt-mp/publish-token/{doomed['id']}", headers=h)
        assert again.status_code == 404


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
