"""Ratings & reviews API.

Rules (plan 005 / guidelines §4):
- Only certified users (users.certified_at set via the Luna handshake) may
  create reviews.
- One review per user per plugin; editing overwrites and marks `edited`.
- Body required for 1–2 star ratings.
- Members of the publishing org cannot review their own plugin.
- One publisher response per review; one helpful vote per user per review.
- Marketplace owners (and global editors) may delete any review.
- Rate limit: 10 review writes per user per day.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_optional_user, is_global_editor
from ..database import get_db
from ..models.db import (
    Marketplace,
    OrgMember,
    Plugin,
    Review,
    ReviewVote,
    User,
    now_ts,
)
from ..models.schemas import (
    PublisherResponseCreate,
    ReviewCreate,
    ReviewResponse,
    ReviewSummary,
)

router = APIRouter()

DAILY_REVIEW_LIMIT = 10


async def _get_plugin(mp_slug: str, plugin_name: str, db: AsyncSession) -> tuple[Marketplace, Plugin]:
    result = await db.execute(select(Marketplace).where(Marketplace.slug == mp_slug))
    mp = result.scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Marketplace not found")
    p_result = await db.execute(
        select(Plugin).where(Plugin.marketplace_id == mp.id, Plugin.name == plugin_name)
    )
    plugin = p_result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(404, "Plugin not found")
    return mp, plugin


async def _recompute_rating(db: AsyncSession, plugin: Plugin) -> None:
    result = await db.execute(
        select(func.count(Review.id), func.avg(Review.rating)).where(Review.plugin_id == plugin.id)
    )
    count, avg = result.one()
    plugin.rating_count = int(count or 0)
    plugin.rating_average = round(float(avg), 2) if avg is not None else 0.0


async def _is_org_member(db: AsyncSession, org_id: str, user_id: str) -> bool:
    result = await db.execute(
        select(OrgMember.id).where(OrgMember.org_id == org_id, OrgMember.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


def _review_response(
    r: Review,
    author: str,
    user: User | None,
    voted_ids: set[str],
) -> ReviewResponse:
    return ReviewResponse(
        id=r.id,
        plugin_id=r.plugin_id,
        rating=r.rating,
        title=r.title or "",
        body=r.body or "",
        author=author,
        plugin_version=r.plugin_version or "",
        helpful_count=r.helpful_count or 0,
        created_at=r.created_at,
        updated_at=r.updated_at,
        edited=r.edited,
        response_body=r.response_body,
        response_at=r.response_at,
        is_mine=bool(user and r.user_id == user.id),
        voted_helpful=r.id in voted_ids,
    )


@router.get("/catalog/{mp_slug}/{plugin_name}/reviews")
async def list_reviews(
    mp_slug: str,
    plugin_name: str,
    sort: str = Query("helpful"),  # helpful | recent | critical
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Reviews for a plugin, with summary (average + star histogram)."""
    _mp, plugin = await _get_plugin(mp_slug, plugin_name, db)

    query = select(Review, User.username).join(User, Review.user_id == User.id).where(
        Review.plugin_id == plugin.id
    )
    if sort == "recent":
        query = query.order_by(Review.created_at.desc())
    elif sort == "critical":
        query = query.order_by(Review.rating.asc(), Review.helpful_count.desc())
    else:
        query = query.order_by(Review.helpful_count.desc(), Review.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)
    rows = (await db.execute(query)).all()

    hist_rows = (
        await db.execute(
            select(Review.rating, func.count(Review.id))
            .where(Review.plugin_id == plugin.id)
            .group_by(Review.rating)
        )
    ).all()
    histogram = {star: 0 for star in range(1, 6)}
    for star, count in hist_rows:
        histogram[int(star)] = int(count)

    voted_ids: set[str] = set()
    if user and rows:
        votes = await db.execute(
            select(ReviewVote.review_id).where(
                ReviewVote.user_id == user.id,
                ReviewVote.review_id.in_([r.id for r, _ in rows]),
            )
        )
        voted_ids = {v for (v,) in votes.all()}

    return {
        "summary": ReviewSummary(
            average=plugin.rating_average or 0.0,
            count=plugin.rating_count or 0,
            histogram=histogram,
        ),
        "reviews": [_review_response(r, username, user, voted_ids) for r, username in rows],
        "page": page,
        "per_page": per_page,
        "can_review": bool(user and user.certified_at),
    }


@router.post("/catalog/{mp_slug}/{plugin_name}/reviews", response_model=ReviewResponse)
async def write_review(
    mp_slug: str,
    plugin_name: str,
    data: ReviewCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update the caller's review (one per user per plugin)."""
    mp, plugin = await _get_plugin(mp_slug, plugin_name, db)

    if not user.certified_at:
        raise HTTPException(
            403,
            "Reviews require a certified account. Link your Luna install "
            "(Settings → Marketplace in Luna) to become certified.",
        )
    if await _is_org_member(db, mp.org_id, user.id):
        raise HTTPException(403, "Members of the publishing account cannot review their own plugin")
    if data.rating <= 2 and not data.body.strip():
        raise HTTPException(400, "A written explanation is required for 1-2 star ratings")

    day_ago = now_ts() - 86400
    recent = await db.execute(
        select(func.count(Review.id)).where(Review.user_id == user.id, Review.updated_at >= day_ago)
    )
    if int(recent.scalar() or 0) >= DAILY_REVIEW_LIMIT:
        raise HTTPException(429, "Review rate limit reached (10/day)")

    existing = (
        await db.execute(
            select(Review).where(Review.plugin_id == plugin.id, Review.user_id == user.id)
        )
    ).scalar_one_or_none()

    if existing:
        existing.rating = data.rating
        existing.title = data.title
        existing.body = data.body
        existing.plugin_version = plugin.latest_version or ""
        existing.updated_at = now_ts()
        existing.edited = True
        review = existing
    else:
        review = Review(
            id=str(uuid.uuid4()),
            plugin_id=plugin.id,
            user_id=user.id,
            rating=data.rating,
            title=data.title,
            body=data.body,
            plugin_version=plugin.latest_version or "",
        )
        db.add(review)
        await db.flush()

    await _recompute_rating(db, plugin)
    await db.commit()
    await db.refresh(review)
    return _review_response(review, user.username, user, set())


@router.delete("/catalog/{mp_slug}/{plugin_name}/reviews/{review_id}")
async def delete_review(
    mp_slug: str,
    plugin_name: str,
    review_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a review: the author, the marketplace owner, or a global editor."""
    mp, plugin = await _get_plugin(mp_slug, plugin_name, db)
    review = await db.get(Review, review_id)
    if review is None or review.plugin_id != plugin.id:
        raise HTTPException(404, "Review not found")

    allowed = review.user_id == user.id or is_global_editor(user)
    if not allowed:
        owner = await db.execute(
            select(OrgMember.id).where(
                OrgMember.org_id == mp.org_id,
                OrgMember.user_id == user.id,
                OrgMember.role == "owner",
            ).limit(1)
        )
        allowed = owner.scalar_one_or_none() is not None
    if not allowed:
        raise HTTPException(403, "Not allowed to delete this review")

    votes = await db.execute(select(ReviewVote).where(ReviewVote.review_id == review.id))
    for v in votes.scalars():
        await db.delete(v)
    await db.delete(review)
    await db.flush()
    await _recompute_rating(db, plugin)
    await db.commit()
    return {"status": "deleted", "review_id": review_id}


@router.post("/catalog/{mp_slug}/{plugin_name}/reviews/{review_id}/helpful")
async def toggle_helpful(
    mp_slug: str,
    plugin_name: str,
    review_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the caller's 'helpful' vote on a review."""
    _mp, plugin = await _get_plugin(mp_slug, plugin_name, db)
    review = await db.get(Review, review_id)
    if review is None or review.plugin_id != plugin.id:
        raise HTTPException(404, "Review not found")
    if review.user_id == user.id:
        raise HTTPException(400, "Cannot vote on your own review")

    existing = (
        await db.execute(
            select(ReviewVote).where(ReviewVote.review_id == review.id, ReviewVote.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        review.helpful_count = max(0, (review.helpful_count or 0) - 1)
        voted = False
    else:
        db.add(ReviewVote(id=str(uuid.uuid4()), review_id=review.id, user_id=user.id))
        review.helpful_count = (review.helpful_count or 0) + 1
        voted = True
    await db.commit()
    return {"voted": voted, "helpful_count": review.helpful_count}


@router.post("/catalog/{mp_slug}/{plugin_name}/reviews/{review_id}/response")
async def publisher_response(
    mp_slug: str,
    plugin_name: str,
    review_id: str,
    data: PublisherResponseCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Post (or replace) the single publisher response under a review."""
    mp, plugin = await _get_plugin(mp_slug, plugin_name, db)
    review = await db.get(Review, review_id)
    if review is None or review.plugin_id != plugin.id:
        raise HTTPException(404, "Review not found")

    if not (is_global_editor(user) or await _is_org_member(db, mp.org_id, user.id)):
        raise HTTPException(403, "Only the publishing account may respond to reviews")

    review.response_body = data.body
    review.response_at = now_ts()
    await db.commit()
    return {"status": "responded", "review_id": review_id}
