"""
Standardized pagination utilities for API endpoints.
"""

from typing import TypeVar, Generic, List, Optional
from pydantic import BaseModel, Field
from fastapi import Query

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Standard pagination parameters."""

    skip: int = Field(0, ge=0, description="Number of items to skip")
    limit: int = Field(
        50, ge=1, le=100, description="Maximum number of items to return"
    )


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated response format."""

    items: List[T]
    total: int
    skip: int
    limit: int
    has_more: bool


def get_pagination_params(
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of items to return (max 100)"
    ),
) -> PaginationParams:
    """
    Dependency to get pagination parameters from query string.

    Usage:
        @router.get("/items")
        async def list_items(
            pagination: PaginationParams = Depends(get_pagination_params)
        ):
            pass
    """
    return PaginationParams(skip=skip, limit=limit)


async def paginate_query(query, db, pagination: PaginationParams) -> tuple[List, int]:
    """
    Helper to paginate a SQLAlchemy query.

    Args:
        query: SQLAlchemy select query
        db: Database session
        pagination: Pagination parameters

    Returns:
        Tuple of (paginated_items, total_count)
    """
    # Get total count
    from sqlalchemy import func, select

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    paginated_query = query.offset(pagination.skip).limit(pagination.limit)
    result = await db.execute(paginated_query)
    items = result.scalars().all()

    return list(items), total


def create_paginated_response(
    items: List[T], total: int, pagination: PaginationParams
) -> PaginatedResponse[T]:
    """
    Create a standardized paginated response.

    Args:
        items: List of items for current page
        total: Total count of all items
        pagination: Pagination parameters used

    Returns:
        PaginatedResponse with metadata
    """
    return PaginatedResponse(
        items=items,
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
        has_more=(pagination.skip + len(items)) < total,
    )
