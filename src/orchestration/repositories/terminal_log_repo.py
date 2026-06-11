from typing import List, Optional
from uuid import UUID

from orchestration.repositories.base_repo import BaseRepository


class TerminalLogRepository(BaseRepository):
    """
    Repository for persisting deployment terminal logs.

    Logs are captured when a deployment transitions to FAILED, STOPPED,
    or TERMINATED so that they remain accessible after the live stream ends.
    """

    async def save(
        self,
        *,
        deployment_id: UUID,
        log_lines: List[str],
        trigger_event: str,
        tx=None,
    ) -> None:
        q = """
        INSERT INTO deployment_terminal_logs
            (deployment_id, log_lines, trigger_event)
        VALUES ($1, $2, $3)
        """
        conn = tx or self.db
        if tx:
            await conn.execute(q, deployment_id, log_lines, trigger_event)
        else:
            async with self.db.acquire() as c:
                await c.execute(q, deployment_id, log_lines, trigger_event)

    async def get_by_deployment(
        self,
        deployment_id: UUID,
    ) -> Optional[dict]:
        """Return the most recent terminal log snapshot for a deployment."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, deployment_id, log_lines, captured_at, trigger_event
                FROM deployment_terminal_logs
                WHERE deployment_id = $1
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                deployment_id,
            )
        return dict(row) if row else None

    async def get_all_by_deployment(
        self,
        deployment_id: UUID,
    ) -> List[dict]:
        """Return all terminal log snapshots for a deployment."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, deployment_id, log_lines, captured_at, trigger_event
                FROM deployment_terminal_logs
                WHERE deployment_id = $1
                ORDER BY captured_at DESC
                """,
                deployment_id,
            )
        return [dict(r) for r in rows]
