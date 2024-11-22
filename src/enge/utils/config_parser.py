#!/usr/bin/env python3

import configparser
import logging
import os
import sys

LOGGER = logging.getLogger(__name__)


def load_config(paths):
    """Try to load config file from the given paths."""
    config = configparser.ConfigParser()
    expanded_paths = (os.path.expanduser(path) for path in paths)
    for path in expanded_paths:
        if os.path.exists(path):
            LOGGER.debug(f"Loading the configuration file from: {path}")
            config.read(path)
            return config
    else:
        LOGGER.critical(
            f"No config file found on any of the expected paths: {list(expanded_paths)}"
        )
        sys.exit(99)
