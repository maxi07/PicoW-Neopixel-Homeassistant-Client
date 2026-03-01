"""
Logging module for PicoW NeoPixel Controller
Supports console and file logging with different log levels
Thread-safe via _thread lock to prevent USB serial crashes on dual-core RP2040
"""
import time
import _thread


class Logger:
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3

    LEVEL_NAMES = {
        DEBUG: "DEBUG",
        INFO: "INFO",
        WARNING: "WARNING",
        ERROR: "ERROR"
    }

    # Shared lock across all Logger instances — protects print() and file I/O
    _lock = _thread.allocate_lock()

    def __init__(self, name, log_file="logs.txt", console_level=DEBUG, file_level=WARNING):
        self.name = name
        self.log_file = log_file
        self.console_level = console_level
        self.file_level = file_level

    def _format_message(self, level, message):
        """Format log message with timestamp"""
        try:
            timestamp = time.localtime()
            time_str = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                timestamp[0], timestamp[1], timestamp[2],
                timestamp[3], timestamp[4], timestamp[5]
            )
        except Exception:
            time_str = "0000-00-00 00:00:00"

        level_name = self.LEVEL_NAMES.get(level, "UNKNOWN")
        return "[{}] [{}] [{}] {}".format(time_str, level_name, self.name, message)

    def _log(self, level, message):
        """Internal logging method (thread-safe)"""
        formatted = self._format_message(level, message)

        Logger._lock.acquire()
        try:
            # Console logging
            if level >= self.console_level:
                print(formatted)

            # File logging
            if level >= self.file_level:
                try:
                    with open(self.log_file, "a") as f:
                        f.write(formatted + "\n")
                except Exception as e:
                    print("Failed to write to log file: {}".format(e))
        finally:
            Logger._lock.release()

    def debug(self, message):
        """Log debug message"""
        self._log(self.DEBUG, message)

    def info(self, message):
        """Log info message"""
        self._log(self.INFO, message)

    def warning(self, message):
        """Log warning message"""
        self._log(self.WARNING, message)

    def error(self, message):
        """Log error message"""
        self._log(self.ERROR, message)
