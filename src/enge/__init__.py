#!/usr/bin/env python3

import logging
from enge.utils.arg_parser import args
from enge.utils import FormatText


class ColorizedFormatter(logging.Formatter):
    def format(self, record):
        log_message = super().format(record)

        if record.levelname == "WARNING":
            log_message = FormatText.format_text(
                log_message, text_col=FormatText.yellow
            )
        elif record.levelname == "ERROR":
            log_message = FormatText.format_text(
                log_message, text_col=FormatText.red, bold=True
            )
        elif record.levelname == "CRITICAL":
            log_message = FormatText.format_text(log_message, text_col=FormatText.red)
        elif record.levelname == "DEBUG":
            log_message = FormatText.format_text(log_message)
        elif record.levelname == "INFO":
            log_message = FormatText.format_text(log_message)

        return log_message


loglevel = logging.INFO
logformat = "%(levelname)-8s | %(message)s"
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests_gssapi").setLevel(logging.WARNING)


if args.debug:
    loglevel = logging.DEBUG

logging.basicConfig(
    level=loglevel,
    format=logformat,
)

logger = logging.getLogger()

console_handler = logging.StreamHandler()
console_handler.setFormatter(ColorizedFormatter(logformat))

logger.handlers = []
logger.addHandler(console_handler)
