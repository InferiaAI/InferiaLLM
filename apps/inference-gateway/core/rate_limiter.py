
import time
from typing import Dict, Tuple

class TokenBucketLimiter:
    """
    In-Memory Token Bucket Rate Limiter.
    Thread-safe enough for async (single process).
    For multi-process, use Redis.
    """
    def __init__(self):
        # Key -> (tokens, last_refill_timestamp)
        self.buckets: Dict[str, Tuple[float, float]] = {}
    
    def check_limit(self, key: str, rpm: int, cost: int = 1) -> Tuple[bool, float]:
        """
        Check if request is allowed.
        Returns (is_allowed, wait_time_seconds).
        wait_time_seconds is 0.0 if allowed.
        """
        if rpm <= 0:
            return True, 0.0 # No limit
            
        now = time.time()
        bucket = self.buckets.get(key)
        
        # Max tokens = RPM (simple burst policy)
        # Refill rate = RPM / 60.0 tokens per second
        max_tokens = float(rpm)
        refill_rate = rpm / 60.0
        
        if not bucket:
            # First request: Full bucket minus cost
            self.buckets[key] = (max_tokens - cost, now)
            return True, 0.0
            
        tokens, last_refill = bucket
        
        # Refill tokens
        time_passed = now - last_refill
        refill_amount = time_passed * refill_rate
        tokens = min(max_tokens, tokens + refill_amount)
        
        if tokens >= cost:
            # Allowed
            self.buckets[key] = (tokens - cost, now)
            return True, 0.0
        else:
            # Denied
            # Calculate wait time: tokens needed = cost - current_tokens (assuming tokens < cost)
            needed = cost - tokens
            wait_time = needed / refill_rate if refill_rate > 0 else 60.0
            
            # Update timestamp to prevent credit accumulation during blocked time? 
            # Actually standard token bucket just waits. We don't update state on denial essentially,
            # except maybe to clamp time?
            # Ideally we just don't touch bucket on failure, so next check calculates refill again.
            # But earlier implementation updated timestamp. Let's stick to standard: don't consume.
            
            # However, if we don't update key, `last_refill` stays old.
            # Next check will see larger `time_passed`, adding more tokens.
            # This is correct.
            
            return False, wait_time

rate_limiter = TokenBucketLimiter()
