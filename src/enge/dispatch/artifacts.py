import logging
from typing import List, Dict, Any


LOGGER = logging.getLogger(__name__)


class ArtifactResolver:
    def resolve_builds(self, compose_name: str) -> List[Dict[str, Any]]:
        """
        Return a list of build dicts based on the active artifact configuration
        (copr/brew/compose) available in parsed_opts.
        """
        try:
            from enge.dispatch.__main__ import get_artifact_info  # reuse existing logic

            return get_artifact_info(compose_name)
        except Exception as e:
            LOGGER.critical(f"Failed to resolve artifacts: {e}")
            raise
