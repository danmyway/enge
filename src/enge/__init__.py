#!/usr/bin/env python3

import logging

# Library-safe: do not configure global logging here. CLI entrypoints handle setup.
# Hides urllib3's connect-retry WARNINGs; never lower this (it also keeps auth
# headers out of -vv output).
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("requests_gssapi").setLevel(logging.WARNING)
logging.getLogger("koji").setLevel(logging.WARNING)
