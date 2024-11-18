#!/usr/bin/env python3

import os
import sys
import configparser
import logging

LOGGER = logging.getLogger(__name__)


def load_config(paths):
    """Try to load config file from the given paths."""
    config = configparser.ConfigParser()
    expanded_paths = (os.path.expanduser(path) for path in paths)
    for path in expanded_paths:
        if os.path.exists(path):
            LOGGER.info(f"Loading the config file from: {path}")
            config.read(path)
            return config
    else:
        LOGGER.critical(
            f"No config file found on any of the expected paths: {list(expanded_paths)}"
        )
        sys.exit(99)
