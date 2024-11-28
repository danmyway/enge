#!/usr/bin/env python3

import logging
from enge.utils.arg_parser import args


loglevel = logging.INFO
logformat = "%(levelname)-8s | %(message)s"
logging.getLogger("urllib3").setLevel(logging.WARNING)

if args.debug:
    loglevel = logging.DEBUG

logging.basicConfig(
    level=loglevel,
    format=logformat,
)
