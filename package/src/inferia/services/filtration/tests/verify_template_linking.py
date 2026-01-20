import asyncio
import httpx
import json
import os

BASE_URL = "http://localhost:8000"

# Mock Data
MOCK_API_KEY = "sk-test-linking-key"
MOCK_DEPLOYMENT_ID = "dep-test-link"
ORG_ID_MOCK = "org-123" # In real integration tests we need to setup DB state. 
# Since we can't easily setup full DB state from here without relying on prior scripts or seed, 
# we will rely on the fact that existing endpoints are working if we bypass auth or use valid key.
# But `resolve_context` needs REAL DB entries.

# Alternative: Test creating a config and check if it persists.
# We will simulate the management API call using the internal key (which might fail if management requires user context).
# Management API requires a logged in user unless we hack it.

# Let's verify by just inspecting the DB or trust the unit test logic? 
# The user wants "Verify Linking". 
# Let's try to simulate the full flow if possible, or at least the API interaction.

# Prerequisite: We need a valid JWT token for Management API. 
# We don't have one easily unless we login.
# Let's try to login as admin if we know credentials, or skip strict auth for this test dev env? 
# The dev environment middleware might be strict.

# Let's just create a test that calls the internal context resolve if we can insert a policy manually?
# Or we can write a script that purely interacts with the DB directly to insert policy and then call API?
# Accessing DB directly from script is possible if we import models.

from db.models import Policy as DBPolicy, Deployment as DBDeployment, User as DBUser, ApiKey as DBApiKey
from rbac.auth import auth_service
from sqlalchemy.future import select

async def verify_linking():
    async with get_async_session_context() as db:
        print("Setting up test data...")
        # 1. Find or Create Org/User
        # Just grab the first user
        stmt = select(DBUser)
        result = await db.execute(stmt)
        user = result.scalars().first()
        if not user:
            print("FAIL: No user found to attach policy to")
            return
            
        org_id = user.org_id
        
        # 2. Create a dummy deployment
        dep_id = "dep_verification_link"
        # Check if exists (Clean up first)
        # We can try to delete using ORM logic or raw sql with text()
        # Let's simple delete
        from sqlalchemy import text
        await db.execute(text(f"DELETE FROM policies WHERE deployment_id='{dep_id}'"))
        await db.execute(text(f"DELETE FROM api_keys WHERE deployment_id='{dep_id}'"))
        await db.execute(text(f"DELETE FROM deployments WHERE id='{dep_id}'"))
        await db.commit()
        
        new_dep = DBDeployment(
            id=dep_id,
            name="Verification Dep",
            model_name="verify-model",
            org_id=org_id,
            provider="openai",
            endpoint_url="http://test",
            credentials_json={}
        )
        db.add(new_dep)
        
        # 3. Create an API Key for this deployment
        raw_key = "sk-verify-link-123"
        key_hash = auth_service.get_password_hash(raw_key)
        prefix = raw_key[:6] + "..."
        
        new_key = DBApiKey(
            name="Verify Key",
            key_hash=key_hash,
            prefix=prefix,
            org_id=org_id,
            deployment_id=dep_id
        )
        db.add(new_key)
        await db.commit()
        
        print("Test data created. Linking template...")
        
        # 4. Link a Template (Direct DB insertion to simulate Management API success)
        # We assume Management API works (it uses standard DB ops). 
        # We strictly want to test if RESOLVE CONTEXT picks it up.
        
        policy_config = {
            "id": "linked-template-id",
            "content": "You are a linked assistant.",
            "enabled": True
        }
        
        policy = DBPolicy(
            org_id=org_id,
            deployment_id=dep_id,
            policy_type="prompt_template",
            config_json=policy_config
        )
        db.add(policy)
        await db.commit()
        
        print("Policy linked. Testing Context Resolution...")
        
        # 5. Call internal resolve endpoint
        headers = {
            "X-Internal-API-Key": "dev-internal-key-change-in-prod", # From env
            "Content-Type": "application/json"
        }
        
        payload = {
            "api_key": raw_key,
            "model": "verify-model"
        }
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.post(f"{BASE_URL}/internal/context/resolve", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                
                print("Resolve Response:", json.dumps(data, indent=2))
                
                tmpl_cfg = data.get("template_config")
                if tmpl_cfg and tmpl_cfg.get("id") == "linked-template-id":
                    print("PASS: Linked template found in context")
                else:
                    print(f"FAIL: Template not found. Got: {tmpl_cfg}")
                    
            except Exception as e:
                print(f"FAIL: API Request failed: {e}")
                
        # Cleanup
        print("Cleaning up...")
        # (Optional)

# Helper for DB Context
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from contextlib import asynccontextmanager

# Use the actual DB URL
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:password@localhost:5432/dbname")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@asynccontextmanager
async def get_async_session_context():
    async with AsyncSessionLocal() as session:
        yield session

if __name__ == "__main__":
    asyncio.run(verify_linking())
