import logging
from typing import List, Dict, Any

from enge.utils.errors import ArtifactResolutionError


LOGGER = logging.getLogger(__name__)


class ArtifactResolver:
    def resolve_builds(self, compose_name: str, ctx) -> List[Dict[str, Any]]:
        """
        Return a list of build dicts based on the active artifact configuration
        available in ctx.
        """
        try:
            from enge.dispatch.__main__ import get_artifact_info  # reuse existing logic

            return get_artifact_info(compose_name, ctx)
        except ArtifactResolutionError:
            # Recoverable: the caller records one failed request and the run
            # continues to exit 2. Logging it CRITICAL here would contradict
            # that contract and report one event twice.
            raise
        except Exception as e:
            LOGGER.critical(f"Failed to resolve artifacts: {e}")
            raise
