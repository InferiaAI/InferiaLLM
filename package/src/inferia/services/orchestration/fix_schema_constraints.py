
import asyncio
import os
import asyncpg

# Use the same DSN as the app
# Check .env or specific env vars
# Using os.getenv attempts to find it. 
# Attempt to read .env file manualy if needed or just use default specific to this user/setup if I can find it.
# Actually, the user's `config.py` in orchestration service has defaults.
# Let's verify `services/orchestration/app/core/config.py` or similar.
# But for now, I will try to use the `os.getenv` correctly, assuming specific env vars might be set in `.env`.
from dotenv import load_dotenv
load_dotenv()

# DSN from orchestrator.sh
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://inferia:inferia@localhost:5432/inferia")

async def fix_constraints():
    print(f"Connecting to {POSTGRES_DSN}...")
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    try:
        print("Fixing Foreign Key constraints...")
        
        # 1. Policies Table
        print("1. Fixing 'policies' table...")
        await conn.execute("ALTER TABLE policies DROP CONSTRAINT IF EXISTS policies_deployment_id_fkey")
        
        # Change column type to UUID
        print("   - Converting policies.deployment_id to UUID...")
        try:
            await conn.execute("ALTER TABLE policies ALTER COLUMN deployment_id TYPE uuid USING deployment_id::uuid")
        except Exception as e:
            print(f"   - Error converting policies.deployment_id: {e}")

        try:
             await conn.execute("""
                ALTER TABLE policies 
                ADD CONSTRAINT policies_deployment_id_fkey 
                FOREIGN KEY (deployment_id) 
                REFERENCES model_deployments(deployment_id)
                ON DELETE SET NULL
            """)
             print("   - 'policies' constraint updated successfully.")
        except Exception as e:
             print(f"   - Error updating 'policies' constraint: {e}")

        # 2. Inference Logs Table
        print("2. Fixing 'inference_logs' table...")
        await conn.execute("ALTER TABLE inference_logs DROP CONSTRAINT IF EXISTS inference_logs_deployment_id_fkey")
        
        # Change column type to UUID
        print("   - Converting inference_logs.deployment_id to UUID...")
        try:
            await conn.execute("ALTER TABLE inference_logs ALTER COLUMN deployment_id TYPE uuid USING deployment_id::uuid")
        except Exception as e:
            print(f"   - Error converting inference_logs.deployment_id: {e}")

        try:
            await conn.execute("""
                ALTER TABLE inference_logs 
                ADD CONSTRAINT inference_logs_deployment_id_fkey 
                FOREIGN KEY (deployment_id) 
                REFERENCES model_deployments(deployment_id)
                ON DELETE CASCADE
            """)
            print("   - 'inference_logs' constraint updated successfully.")
        except Exception as e:
            # It might fail if existing data violates the new constraint
            print(f"   - Warning: Could not add constraint to 'inference_logs' (possible data mismatch): {e}")

        # 3. API Keys Table
        print("3. Fixing 'api_keys' table...")
        await conn.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_deployment_id_fkey")
        
        # Change column type to UUID
        print("   - Converting api_keys.deployment_id to UUID...")
        try:
             await conn.execute("ALTER TABLE api_keys ALTER COLUMN deployment_id TYPE uuid USING deployment_id::uuid")
        except Exception as e:
            print(f"   - Error converting api_keys.deployment_id: {e}")

        try:
            await conn.execute("""
                ALTER TABLE api_keys 
                ADD CONSTRAINT api_keys_deployment_id_fkey 
                FOREIGN KEY (deployment_id) 
                REFERENCES model_deployments(deployment_id)
                ON DELETE SET NULL
            """)
            print("   - 'api_keys' constraint updated successfully.")
        except Exception as e:
            print(f"   - Warning: Could not add constraint to 'api_keys': {e}")
            
        print("\nDone.")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(fix_constraints())
