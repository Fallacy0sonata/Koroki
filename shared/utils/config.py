import os
import yaml
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=1)
def get_settings() -> dict:
    """
    Load and cache config/settings.yaml from the repo root.

    Repo root is resolved from the KOROKI_ROOT env var first,
    then by walking up from this file's location as fallback.
    """
    root_env = os.environ.get("KOROKI_ROOT")
    if root_env:
        config_path = Path(root_env) / "config" / "settings.yaml"
    else:
        # Walk up from shared/utils/ to find config/
        config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"

    if not config_path.exists():
        raise FileNotFoundError(
            f"settings.yaml not found at {config_path}. "
            "Set KOROKI_ROOT env var to the repo root."
        )

    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def reload_settings() -> dict:
    """Force reload settings (clears cache). Use only in tests or config hot-reloads."""
    get_settings.cache_clear()
    return get_settings()
