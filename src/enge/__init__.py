#!/usr/bin/env python3

import logging

# Library-safe: do not configure global logging here. CLI entrypoints handle setup.
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests_gssapi").setLevel(logging.WARNING)
logging.getLogger("koji").setLevel(logging.WARNING)
