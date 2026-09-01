"""
Centralized timezone handling for appointment booking system.

Provides utilities for:
- Timezone validation
- Creating timezone-aware datetimes
- Converting between timezones
- Handling timezone-aware comparisons
"""

from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo, available_timezones
import logging

logger = logging.getLogger(__name__)

# Valid IANA timezone names
VALID_TIMEZONES = set(available_timezones())

# Common business timezones (reference mapping)
TIMEZONE_MAP = {
    "India": "Asia/Kolkata",
    "US_Eastern": "America/New_York",
    "US_Pacific": "America/Los_Angeles",
    "UK": "Europe/London",
    "Australia": "Australia/Sydney",
    "UAE": "Asia/Dubai",
    "Singapore": "Asia/Singapore",
    "Japan": "Asia/Tokyo",
    "South_Africa": "Africa/Johannesburg",
    "Canada_Eastern": "America/Toronto",
    "Canada_Pacific": "America/Vancouver",
}


def validate_timezone(tz_name: str) -> bool:
    """
    Validate that a timezone name is a valid IANA timezone.
    
    Args:
        tz_name: Timezone name (e.g., 'Asia/Kolkata')
        
    Returns:
        True if valid, False otherwise
    """
    if not tz_name:
        return False
    return tz_name in VALID_TIMEZONES


def get_timezone(tz_name: str):
    """
    Get a ZoneInfo timezone object.
    
    Args:
        tz_name: Timezone name (e.g., 'Asia/Kolkata')
        
    Returns:
        ZoneInfo object
        
    Raises:
        ValueError if timezone is invalid
    """
    if not validate_timezone(tz_name):
        raise ValueError(
            f"Invalid timezone: {tz_name}. Must be a valid IANA timezone."
        )
    
    return ZoneInfo(tz_name)


def make_aware_datetime(
    selected_date: date,
    selected_time: time,
    tz_name: str,
) -> datetime:
    """
    Create a timezone-aware datetime for a given date, time, and timezone.
    
    Args:
        selected_date: Date object
        selected_time: Time object (naive)
        tz_name: IANA timezone name (e.g., 'Asia/Kolkata')
        
    Returns:
        timezone-aware datetime object
        
    Raises:
        ValueError if timezone is invalid
    """
    if not validate_timezone(tz_name):
        logger.error(f"Invalid timezone: {tz_name}")
        raise ValueError(f"Invalid timezone: {tz_name}")
    
    tzinfo = get_timezone(tz_name)
    
    # Combine date and time, then apply timezone
    naive_dt = datetime.combine(selected_date, selected_time)
    aware_dt = naive_dt.replace(tzinfo=tzinfo)
    
    return aware_dt


def ensure_aware_datetime(
    dt: datetime,
    tz_name: str,
) -> datetime:
    """
    Ensure a datetime is timezone-aware.
    
    If already aware, return as-is.
    If naive, apply the given timezone.
    
    Args:
        dt: datetime object (may be naive or aware)
        tz_name: IANA timezone name (fallback if dt is naive)
        
    Returns:
        timezone-aware datetime object
        
    Raises:
        ValueError if timezone is invalid
    """
    if dt.tzinfo is not None:
        return dt
    
    # dt is naive; apply timezone
    if not validate_timezone(tz_name):
        logger.error(f"Invalid timezone for naive datetime conversion: {tz_name}")
        raise ValueError(f"Invalid timezone: {tz_name}")
    
    tzinfo = get_timezone(tz_name)
    return dt.replace(tzinfo=tzinfo)


def convert_to_timezone(dt: datetime, target_tz_name: str) -> datetime:
    """
    Convert a datetime to a different timezone.
    
    Args:
        dt: timezone-aware datetime
        target_tz_name: Target IANA timezone name
        
    Returns:
        datetime in target timezone
        
    Raises:
        ValueError if dt is naive or timezone is invalid
    """
    if dt.tzinfo is None:
        raise ValueError("Cannot convert naive datetime. Must be timezone-aware.")
    
    if not validate_timezone(target_tz_name):
        raise ValueError(f"Invalid target timezone: {target_tz_name}")
    
    target_tz = get_timezone(target_tz_name)
    return dt.astimezone(target_tz)


def overlaps(
    start_at: datetime,
    end_at: datetime,
    existing_start: datetime,
    existing_end: datetime,
) -> bool:
    """
    Check if two time ranges overlap.
    
    All datetimes must be timezone-aware.
    
    Args:
        start_at: Slot start time (aware)
        end_at: Slot end time (aware)
        existing_start: Existing slot start (aware)
        existing_end: Existing slot end (aware)
        
    Returns:
        True if ranges overlap, False otherwise
    """
    return (
        start_at < existing_end
        and end_at > existing_start
    )