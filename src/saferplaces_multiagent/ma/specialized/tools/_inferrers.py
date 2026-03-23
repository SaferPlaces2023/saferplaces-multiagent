"""
Reusable inference helpers for tool arguments.
Simple functions to infer missing fields with sensible defaults.
"""

import datetime
from datetime import timezone
from typing import Optional, Callable


# ============================================================================
# TIME HELPERS
# ============================================================================

def get_now_naive() -> datetime.datetime:
    """Get current UTC time without timezone info."""
    return datetime.datetime.now(tz=timezone.utc).replace(tzinfo=None)


def parse_dt_naive(dt: str | datetime.datetime) -> datetime.datetime:
    """Parse ISO datetime string to naive datetime."""
    if isinstance(dt, datetime.datetime):
        return dt.replace(tzinfo=None)
    return datetime.datetime.fromisoformat(dt).replace(tzinfo=None)

def to_iso_naive(dt: str | datetime.datetime) -> str:
    """Convert naive datetime to ISO format string without timezone info."""
    dt = parse_dt_naive(dt)
    return dt.isoformat().rstrip('Z')


def apply_delay_cap(dt: datetime.datetime, delay_minutes: int) -> datetime.datetime:
    """Cap datetime at current time minus delay."""
    now = get_now_naive()
    max_allowed = now - datetime.timedelta(minutes=delay_minutes)
    return min(dt, max_allowed)


# ============================================================================
# TIME FIELD INFERRERS
# ============================================================================

def infer_time_start(
    default_hours_back: int = 1,
    fallback_field: str = 'time_range',
    delay_minutes: int = 0
) -> Callable:
    """
    Infer time_start from time_range[0] or default.
    
    Args:
        default_hours_back: Hours to go back from now as default
        fallback_field: Field name to check for fallback (expects list)
        delay_minutes: Minutes to cap result from current time
    """
    def inferrer(**kwargs) -> Optional[str]:
        value = kwargs.get('time_start')
        if value:
            value = parse_dt_naive(value)
        else:
            # Try fallback
            fallback = kwargs.get(fallback_field)
            if fallback and isinstance(fallback, list) and fallback[0]:
                value = parse_dt_naive(fallback[0])
            else:
                # Use default
                now = get_now_naive().replace(minute=0, second=0, microsecond=0)
                value = now - datetime.timedelta(hours=default_hours_back)
        
        if delay_minutes > 0:
            value = apply_delay_cap(value, delay_minutes)

        value = to_iso_naive(value)
        return value
    
    return inferrer


def infer_time_end(
    fallback_field: str = 'time_range',
    delay_minutes: int = 0
) -> Callable:
    """
    Infer time_end from time_range[1] or current time.
    
    Args:
        fallback_field: Field name to check for fallback (expects list)
        delay_minutes: Minutes to cap result from current time
    """
    def inferrer(**kwargs) -> Optional[str]:
        value = kwargs.get('time_end')
        if value:
            value = parse_dt_naive(value)
        else:
            # Try fallback
            fallback = kwargs.get(fallback_field)
            if fallback and isinstance(fallback, list) and len(fallback) > 1 and fallback[1]:
                value = parse_dt_naive(fallback[1])
            else:
                # Use current time
                value = get_now_naive().replace(minute=0, second=0, microsecond=0)
        
        if delay_minutes > 0:
            value = apply_delay_cap(value, delay_minutes)
        
        value = to_iso_naive(value)
        return value

    
    return inferrer


def infer_time_range(
    default_hours_back: int = 1,
    delay_minutes: int = 0,
    skip_if_both_present: tuple = ('time_start', 'time_end')
) -> Callable:
    """
    Infer time_range if not provided or if start/end missing.
    
    Args:
        default_hours_back: Hours to go back from now as default start
        delay_minutes: Minutes to cap end time from current time
        skip_if_both_present: Skip inference if these fields both exist
    """
    def inferrer(**kwargs) -> Optional[list]:
        # Skip if both start and end provided
        if skip_if_both_present:
            if all(kwargs.get(field) for field in skip_if_both_present):
                return None
        
        time_range = kwargs.get('time_range')
        now = get_now_naive().replace(minute=0, second=0, microsecond=0)
        
        if time_range is None:
            # Default range
            start = now - datetime.timedelta(hours=default_hours_back)
            end = now
        else:
            # Parse provided range
            start = parse_dt_naive(time_range[0])
            end = parse_dt_naive(time_range[1])
        
        if delay_minutes > 0:
            end = apply_delay_cap(end, delay_minutes)
        
        start_iso = to_iso_naive(start)
        end_iso = to_iso_naive(end)
        return [start_iso, end_iso]
    
    return inferrer
