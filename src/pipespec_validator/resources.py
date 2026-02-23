from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .validator import BUNDLED_SCHEMA_NAME

BUNDLED_PROMPT_PROFILE_NAME = "pipespec_prompt_profile_v1.json"


def _read_package_data(filename: str) -> str:
    from importlib import resources
    return resources.files("pipespec_validator.data").joinpath(filename).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_canonical_schema() -> dict[str, Any]:
    """
    Load the canonical PipeSpec schema (normative) from package data.
    """
    return json.loads(_read_package_data(BUNDLED_SCHEMA_NAME))


@lru_cache(maxsize=1)
def load_prompt_profile() -> dict[str, Any]:
    """
    Load the non-normative prompt profile from package data.

    This is for LLM prompting / tool-schema convenience.
    Do not validate PipeSpec documents against this profile.
    """
    return json.loads(_read_package_data(BUNDLED_PROMPT_PROFILE_NAME))