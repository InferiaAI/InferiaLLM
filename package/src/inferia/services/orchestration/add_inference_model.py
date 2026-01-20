import asyncio
import asyncpg
import os

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://inferia:inferia@localhost:5432/inferia")

async def migrate():
    print(f"Connecting to {POSTGRES_DSN}")
    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        # Check if column exists
        row = await conn.fetchrow("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='model_deployments' AND column_name='inference_model'
        """)
        
        if not row:
            print("Adding inference_model column...")
            await conn.execute("""
                ALTER TABLE model_deployments 
                ADD COLUMN inference_model TEXT
            """)
            print("Column added.")
        else:
            print("Column inference_model already exists.")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(migrate())
