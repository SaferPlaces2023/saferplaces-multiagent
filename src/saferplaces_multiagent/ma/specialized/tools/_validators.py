"""
Reusable validators for tool arguments.
Factory functions that return validator callables.
"""

import datetime
from datetime import timezone
from typing import Optional, Callable


def parse_dt(time_str: str) -> datetime.datetime:
    """Helper to parse ISO datetime string to UTC."""
    return datetime.datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)


def value_in_list(field: str, allowed: list, label: str = "Allowed") -> Callable:
    """
    Validator factory: check if value is in allowed list.
    
    Args:
        field: Name of the field to validate
        allowed: List of allowed values
        label: Label for error message (default: "Allowed")
    
    Returns:
        Validator function that returns error message or None
    """
    def validator(**kwargs) -> Optional[str]:
        value = kwargs.get(field)
        if value not in allowed:
            return f"Invalid {field} '{value}'. {label}: {', '.join(map(str, allowed))}"
        return None
    return validator


def bbox_inside(field: str, reference: dict) -> Callable:
    """
    Validator factory: check if bbox is inside reference bbox.
    
    Args:
        field: Name of the bbox field to validate
        reference: Reference bbox dict with keys: west, south, east, north
    
    Returns:
        Validator function that returns error message or None
    """
    def validator(**kwargs) -> Optional[str]:
        bbox = kwargs.get(field)
        if not bbox:
            return None
        if (bbox['west'] < reference['west'] or bbox['south'] < reference['south'] or
            bbox['east'] > reference['east'] or bbox['north'] > reference['north']):
            return f"Bounding box {bbox} exceeds reference {reference}"
        return None
    return validator


def time_within_days(field: str, days: int) -> Callable:
    """
    Validator factory: check if time is within last N days.
    
    Args:
        field: Name of the time field to validate
        days: Number of days to check
    
    Returns:
        Validator function that returns error message or None
    """
    min_time = datetime.datetime.now(tz=timezone.utc) - datetime.timedelta(days=days)
    
    def validator(**kwargs) -> Optional[str]:
        time_str = kwargs.get(field)
        if not time_str:
            return None
        if parse_dt(time_str) < min_time:
            field_label = field.replace('_', ' ').title()
            return f"{field_label} {time_str} too old. Data available for last {days} days only"
        return None
    return validator

def time_before(field: str, other_field: str) -> Callable:
    """
    Validator factory: check if time is before another time field.
    
    Args:
        field: Name of the time field to validate
        other_field: Name of the other time field to compare against
    
    Returns:
        Validator function that returns error message or None
    """
    def validator(**kwargs) -> Optional[str]:
        time_str = kwargs.get(field)
        other_time_str = kwargs.get(other_field)
        if not (time_str and other_time_str):
            return None
        if parse_dt(time_str) >= parse_dt(other_time_str):
            field_label = field.replace('_', ' ').title()
            other_label = other_field.replace('_', ' ').title()
            return f"{field_label} {time_str} must be before {other_label} {other_time_str}"
        return None
    return validator


def time_before_datetime(field: str, reference_time: datetime.datetime, label: str = "current time") -> Callable:
    """
    Validator factory: check if time is before reference.
    
    Args:
        field: Name of the time field to validate
        reference_time: Reference datetime to compare against
        label: Label for the reference time in error message (default: "current time")
    
    Returns:
        Validator function that returns error message or None
    """
    def validator(**kwargs) -> Optional[str]:
        time_str = kwargs.get(field)
        if not time_str:
            return None
        if parse_dt(time_str) > reference_time:
            field_label = field.replace('_', ' ').title()
            return f"{field_label} {time_str} cannot be after {label}"
        return None
    return validator


def time_after(field: str, other_field: str) -> Callable:
    """
    Validator factory: check if time is after another time field.
    
    Args:
        field: Name of the time field to validate
        other_field: Name of the other time field to compare against
    
    Returns:
        Validator function that returns error message or None
    """
    def validator(**kwargs) -> Optional[str]:
        time_str = kwargs.get(field)
        other_time_str = kwargs.get(other_field)
        if not (time_str and other_time_str):
            return None
        if parse_dt(time_str) <= parse_dt(other_time_str):
            field_label = field.replace('_', ' ').title()
            other_label = other_field.replace('_', ' ')
            return f"{field_label} {time_str} must be after {other_label} {other_time_str}"
        return None
    return validator

def time_after_datetime(field: str, reference_date: datetime.date, label: str = "current date") -> Callable:
    """
    Validator factory: check if time is after a specific date.
    
    Args:
        field: Name of the time field to validate
        reference_date: Reference date to compare against
        label: Label for the reference date in error message (default: "current date")
    
    Returns:
        Validator function that returns error message or None
    """
    def validator(**kwargs) -> Optional[str]:
        time_str = kwargs.get(field)
        if not time_str:
            return None
        if parse_dt(time_str).date() <= reference_date:
            field_label = field.replace('_', ' ').title()
            return f"{field_label} {time_str} must be after {label} {reference_date}"
        return None
    return validator