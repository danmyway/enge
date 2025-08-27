#!/usr/bin/env python3

import logging

from enge.utils import FormatText


class ColorizedFormatter(logging.Formatter):
    def format(self, record):
        log_message = super().format(record)

        if record.levelname == "WARNING":
            log_message = FormatText.format_text(
                log_message, text_col=FormatText.YELLOW
            )
        elif record.levelname == "ERROR":
            log_message = FormatText.format_text(
                log_message, text_col=FormatText.RED, bold=True
            )
        elif record.levelname == "CRITICAL":
            log_message = FormatText.format_text(log_message, text_col=FormatText.RED)
        elif record.levelname == "DEBUG":
            log_message = FormatText.format_text(log_message, text_col=FormatText.DIM)
        elif record.levelname == "INFO":
            log_message = FormatText.format_text(log_message)

        return log_message


# Library-safe: do not configure global logging here. CLI entrypoints handle setup.
logformat = "%(levelname)-8s | %(message)s"
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests_gssapi").setLevel(logging.WARNING)
logging.getLogger("koji").setLevel(logging.WARNING)
