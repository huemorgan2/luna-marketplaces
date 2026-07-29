"""Reviews: certified gate, own-plugin block, rating math, helpful votes,
publisher response, moderation delete."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import init_db
from app.main import app
from app.packaging import package_dir_to_zip

REPO = Path(__file__).resolve().parents[2]
HW2 = REPO / "examples" / "hello_world_2"

MP = "review-mp"
PLUGIN = "hello-world-2"


@pytest.fixture(scope="module", autouse=True)
async def _ready():
    await init_db()
    # Publisher account + marketplace + plugin
    async with _client() as c:
        h = await _signup(c, "pub@example.com", "publisher")
        await c.post("/api/orgs", json={"name": "Pub Co", "slug": "pub-co"}, headers=h)
        await c.post("/api/orgs/pub-co/marketplaces",
                     json={"name": "Review MP", "slug": MP, "visibility": "public"}, headers=h)
        up = await c.post(f"/api/marketplaces/{MP}/upload",
                          files={"artifact": ("p.zip", package_dir_to_zip(HW2), "application/zip")},
                          headers=h)
        assert up.status_code == 200, up.text
    yield


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _signup(c: AsyncClient, email: str, username: str) -> dict:
    await c.post("/api/auth/signup", json={"email": email, "username": username, "password": "pw12345"})
    token = (await c.post("/api/auth/login", json={
        "email": email, "username": username, "password": "pw12345"})).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _certify(c: AsyncClient, h: dict, install_id: str) -> None:
    enroll = (await c.post("/api/luna/enroll", json={"install_id": install_id})).json()
    r = await c.post("/api/me/link-luna", json={"code": enroll["link_code"]}, headers=h)
    assert r.status_code == 200, r.text


async def test_review_lifecycle():
    async with _client() as c:
        h = await _signup(c, "reader@example.com", "reader")

        # uncertified → 403
        r = await c.post(f"/api/catalog/{MP}/{PLUGIN}/reviews",
                         json={"rating": 5, "title": "Great", "body": "Works well."}, headers=h)
        assert r.status_code == 403

        await _certify(c, h, "reader-install-01")

        # low rating without body → 400
        r = await c.post(f"/api/catalog/{MP}/{PLUGIN}/reviews", json={"rating": 1}, headers=h)
        assert r.status_code == 400

        # valid review
        r = await c.post(f"/api/catalog/{MP}/{PLUGIN}/reviews",
                         json={"rating": 4, "title": "Solid", "body": "Does what it says."}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["plugin_version"] == "0.1.0"

        # rating denormalized onto the plugin
        detail = (await c.get(f"/api/catalog/{MP}/{PLUGIN}")).json()
        assert detail["rating_count"] == 1 and detail["rating_average"] == 4.0

        # editing replaces (one review per user), marked edited
        r = await c.post(f"/api/catalog/{MP}/{PLUGIN}/reviews",
                         json={"rating": 5, "title": "Better", "body": "Even better now."}, headers=h)
        assert r.json()["edited"] is True
        listing = (await c.get(f"/api/catalog/{MP}/{PLUGIN}/reviews")).json()
        assert listing["summary"]["count"] == 1
        assert listing["summary"]["average"] == 5.0
        assert listing["summary"]["histogram"]["5"] == 1 or listing["summary"]["histogram"][5] == 1


async def test_publisher_blocked_and_responds():
    async with _client() as c:
        hp = await _signup(c, "pub@example.com", "publisher")
        await _certify(c, hp, "pub-install-01")
        # publisher org member cannot review own plugin, even certified
        r = await c.post(f"/api/catalog/{MP}/{PLUGIN}/reviews",
                         json={"rating": 5, "body": "my own plugin"}, headers=hp)
        assert r.status_code == 403

        # but may respond to an existing review
        listing = (await c.get(f"/api/catalog/{MP}/{PLUGIN}/reviews")).json()
        review_id = listing["reviews"][0]["id"]
        r = await c.post(f"/api/catalog/{MP}/{PLUGIN}/reviews/{review_id}/response",
                         json={"body": "Thanks for the feedback."}, headers=hp)
        assert r.status_code == 200
        listing = (await c.get(f"/api/catalog/{MP}/{PLUGIN}/reviews")).json()
        assert listing["reviews"][0]["response_body"] == "Thanks for the feedback."


async def test_helpful_votes_and_moderation():
    async with _client() as c:
        listing_url = f"/api/catalog/{MP}/{PLUGIN}/reviews"
        review_id = (await _get_first_review_id(c, listing_url))

        voter = await _signup(c, "voter@example.com", "voter")
        # vote → count 1; toggle off → 0; author cannot vote on own review
        r = await c.post(f"{listing_url}/{review_id}/helpful", headers=voter)
        assert r.json() == {"voted": True, "helpful_count": 1}
        r = await c.post(f"{listing_url}/{review_id}/helpful", headers=voter)
        assert r.json() == {"voted": False, "helpful_count": 0}
        author = await _signup(c, "reader@example.com", "reader")
        assert (await c.post(f"{listing_url}/{review_id}/helpful", headers=author)).status_code == 400

        # random user cannot delete someone else's review; owner can
        assert (await c.delete(f"{listing_url}/{review_id}", headers=voter)).status_code == 403
        owner = await _signup(c, "pub@example.com", "publisher")
        assert (await c.delete(f"{listing_url}/{review_id}", headers=owner)).status_code == 200
        detail = (await c.get(f"/api/catalog/{MP}/{PLUGIN}")).json()
        assert detail["rating_count"] == 0


async def _get_first_review_id(c: AsyncClient, listing_url: str) -> str:
    listing = (await c.get(listing_url)).json()
    assert listing["reviews"], "expected a review from earlier tests"
    return listing["reviews"][0]["id"]
