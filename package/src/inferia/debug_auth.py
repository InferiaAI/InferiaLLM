import asyncio
from db.database import AsyncSessionLocal as async_session_maker
from db.models import User
from sqlalchemy import select
from rbac.auth import auth_service
import bcrypt

async def debug_auth():
    async with async_session_maker() as db:
        username = "admin@example.com"
        password = "admin123"
        
        print(f"Checking user: {username}")
        result = await db.execute(select(User).where(User.email == username))
        user = result.scalars().first()
        
        if not user:
            print("❌ User NOT FOUND in database")
            return

        print(f"Found user ID: {user.id}")
        print(f"Stored Hash: {user.password_hash}")
        
        # Verify Password
        is_valid = auth_service.verify_password(password, user.password_hash)
        if is_valid:
            print("✅ Password 'admin123' is VALID")
        else:
            print("❌ Password 'admin123' is INVALID")
            
            # Helper: Generate new hash
            new_hash = auth_service.get_password_hash(password)
            print(f"Expected Hash for 'admin123': {new_hash}")
            
            # Reset Password
            print("Resetting password to 'admin123'...")
            user.password_hash = new_hash
            await db.commit()
            print("✅ Password reset complete.")

if __name__ == "__main__":
    asyncio.run(debug_auth())
