"""External risk profiles configuration.

Risk profiles are loaded from a JSON file mounted as a Docker volume.
This allows changing coefficients without rebuilding containers.

File format:
[
  {"key": "highway", "value": "motorway", "base_risk": 0.1, "radius_m": 200, "geometry_type": "line"},
  ...
]
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

RiskProfile = dict[str, object]
_RiskCache = dict[str, list[RiskProfile]]  # keyed by OSM table join column


def _load_profiles() -> list[RiskProfile]:
    path = Path(settings.risk_profiles_path)
    if not path.exists():
        logger.warning("Risk profiles file not found: %s", path)
        return []
    with open(path) as f:
        return json.load(f)


class RiskConfig:
    """Thread-safe risk profiles loader with change detection."""

    def __init__(self) -> None:
        self._profiles: list[RiskProfile] = []
        self._cache: dict[str, list[RiskProfile]] = {}
        self._etag: str = ""

    def load(self) -> None:
        """Load profiles from file if changed."""
        path = Path(settings.risk_profiles_path)
        if not path.is_file():
            logger.warning("Risk profiles file not found: %s", path)
            return

        current_etag = hashlib.md5(path.read_bytes()).hexdigest()
        if current_etag == self._etag:
            return  # No change

        self._profiles = _load_profiles()
        self._cache.clear()
        self._etag = current_etag
        logger.info("Loaded %d risk profiles from %s", len(self._profiles), path)

    def get_profiles(self) -> list[RiskProfile]:
        """Get all risk profiles."""
        self.load()
        return self._profiles

    def get_lookup(self) -> dict[tuple[str, str], RiskProfile]:
        """Get profiles as (key, value) -> profile dict for fast lookup."""
        self.load()
        return {(p["key"], p["value"]): p for p in self._profiles}


# Module-level singleton
risk_config = RiskConfig()

# Load on import
risk_config.load()
