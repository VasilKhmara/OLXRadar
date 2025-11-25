"""
Sets the format of messages related to the script operation (status and errors),
which will be written to the log.log file, saved in the same directory as the script.
"""
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from utils import BASE_DIR

class TimezoneFormatter(logging.Formatter):
    """Formatter that renders timestamps in the configured timezone."""

    def __init__(self, *args, timezone: ZoneInfo, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.timezone = timezone

    def formatTime(self, record, datefmt=None):
        ts = datetime.fromtimestamp(record.created, tz=self.timezone)
        if datefmt:
            return ts.strftime(datefmt)
        return ts.isoformat(timespec="seconds")


log_file_path = os.path.join(BASE_DIR, "../log.log")
stockholm_timezone = ZoneInfo("Europe/Stockholm")
formatter = TimezoneFormatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    timezone=stockholm_timezone,
    datefmt="%Y-%m-%d %H:%M:%S",
)

file_handler = logging.FileHandler(log_file_path)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.INFO,  # default to INFO so we can see only important messages
    handlers=[file_handler, stream_handler],
)
