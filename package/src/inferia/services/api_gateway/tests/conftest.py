"""Test configuration and fixtures."""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path
import os

project_root = Path(__file__).resolve().parent.parent.parent.parent
gateway_path = project_root / "services" / "api_gateway"
sys.path.insert(0, str(gateway_path))

from app import app
from pathlib import Path
import os

# Add services/api_gateway to path
project_root = Path(__file__).resolve().parent.parent.parent.parent
gateway_path = project_root / "services" / "api_gateway"
sys.path.insert(0, str(gateway_path))

from app import app
from inferia.services.api_gateway.rbac.mock_data import mock_db
from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.db.models import User as DBUser
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from inferia.services.api_gateway.db.database import get_db


@pytest.fixture
def mock_db_session():
    """Mock DB session."""
    session = AsyncMock()
    # Create result mock
    mock_result = MagicMock()
    mock_user = DBUser(
        id="user_admin_001",
        email="admin@inferia.com",
        password_hash="hashed",
        default_org_id="org_default",
        totp_enabled=False,
    )
    mock_result.scalars.return_value.all.return_value = [mock_user]
    mock_result.scalars.return_value.first.return_value = mock_user

    session.execute.return_value = mock_result

    session.close = AsyncMock()
    # Mock context manager behavior for middleware usage
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest_asyncio.fixture
async def client(mock_db_session):
    """Async test client with mocked DB."""
    # Override get_db dependency
    app.dependency_overrides[get_db] = lambda: mock_db_session

    # Patch get_current_user to bypass DB lookup in middleware (and return valid user)
    async def mock_get_current_user(db, token):
        payload = auth_service.decode_token(token)
        user = DBUser(id=payload.sub, email=payload.sub + "@test.com")
        return user, "org_default", payload.roles

    # Patch AsyncSessionLocal for middleware usage
    mock_session_maker = MagicMock(return_value=mock_db_session)

    with (
        patch.object(
            auth_service, "get_current_user", side_effect=mock_get_current_user
        ),
        patch("db.database.AsyncSessionLocal", mock_session_maker),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as ac:
            yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def admin_token(client):
    """Get admin user token."""
    user = DBUser(id="user_admin_001", email="admin@inferia.com")
    # auth_service.create_access_token expects user object with id, email
    return auth_service.create_access_token(user, org_id="org_default", role="admin")


@pytest.fixture
def developer_token(client):
    """Get developer user token."""
    user = DBUser(id="user_dev_001", email="dev@inferia.com")
    return auth_service.create_access_token(
        user, org_id="org_default", role="power_user"
    )


@pytest.fixture
def user_token(client):
    """Get standard user token."""
    user = DBUser(id="user_std_001", email="user@inferia.com")
    return auth_service.create_access_token(
        user, org_id="org_default", role="standard_user"
    )


@pytest.fixture
def guest_token(client):
    """Get guest user token."""
    user = DBUser(id="user_guest_001", email="guest@inferia.com")
    return auth_service.create_access_token(user, org_id="org_default", role="guest")
