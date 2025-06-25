import logging
import os
import sys
from pythonjsonlogger import json


def setup_logger():
    # Get the root logger
    logger = logging.getLogger()

    # Prevent adding handlers multiple times if setup_logger is called more than once
    if logger.handlers:
        return logger

    # Determine log level from environment variable, default to INFO
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logger.setLevel(log_level)

    # Create a stream handler (sends logs to stderr)
    handler = logging.StreamHandler(sys.stderr)

    # Create a JSON formatter for Cloud Logging
    # 'severity' and 'timestamp' are automatically mapped by Cloud Logging if present
    # 'message' is the main log message
    # Add other fields you want to see parsed in Cloud Logging
    formatter = json.JsonFormatter(
        '%(levelname)s %(asctime)s %(name)s %(process)d %(thread)d %(module)s %(funcName)s %(lineno)d %(message)s',
        rename_fields={'levelname': 'severity', 'asctime': 'timestamp'}
    )

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    """
    def handle_exception(exc_type, exc_value, exc_traceback):
         if issubclass(exc_type, KeyboardInterrupt):
             sys.__excepthook__(exc_type, exc_value, exc_traceback)
             return
         logger.critical("Uncaught exception:", exc_info=(exc_type, exc_value, exc_traceback))
    sys.excepthook = handle_exception
    """
    logger.info(f"Logger initialized with level: {logging.getLevelName(logger.level)}")
    return logger


# Call setup_logger() once when the module is imported
# This ensures the logger is configured as soon as it's needed.
app_logger = setup_logger()