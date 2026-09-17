"""Engine recipes: what an engine needs to run, per hardware profile.

Ports, model directories and health paths were three dicts in the Kubernetes
adapter. They are one record here because they describe the engine, not the
provider, and because every new field was becoming a fourth dict.

Loaded through importlib.resources rather than a path relative to __file__:
the control plane runs from an installed wheel, where there is no source tree
to be relative to.
"""

from __future__ import annotations

import logging
import os
from importlib import resources
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

_BUILTIN = "engines.yaml"
_OVERRIDE_DIR_ENV = "INFERIA_RECIPES_DIR"


class UnknownProfile(Exception):
    """A known engine asked for a profile it does not ship.

    Distinct from an unrecognised engine: vLLM on zero GPUs is a real request
    for something that does not exist, and answering it with defaults produces
    a pod that schedules and cannot run.
    """


def _stringify(value):
    """Environment values are strings to Kubernetes whatever YAML made of
    them. Unquoted, YAML reads 2 as an int and `yes` as a bool, and neither
    is a mistake worth failing a deployment over."""
    if not isinstance(value, dict):
        return value
    return {str(k): str(v) for k, v in value.items()}


class EngineProfile(BaseModel):
    """Per-profile overrides. Empty means the engine's own values apply."""

    port: Optional[int] = None
    image: Optional[str] = None
    model_dir: Optional[str] = None
    health_path: Optional[str] = None
    image_pull_policy: Optional[str] = None
    cpu_request: Optional[str] = None
    # A boolean rather than an optional cpu_limit, because null would have to
    # mean both "inherit" and "deliberately no ceiling".
    cpu_burstable: Optional[bool] = None
    env: Dict[str, str] = Field(default_factory=dict)

    _env_str = field_validator("env", mode="before")(_stringify)


class EngineRecipe(BaseModel):
    port: int
    image: Optional[str] = None
    model_dir: Optional[str] = None
    health_path: Optional[str] = None
    image_pull_policy: Optional[str] = None
    cpu_request: Optional[str] = None
    cpu_burstable: Optional[bool] = None
    env: Dict[str, str] = Field(default_factory=dict)
    profiles: Dict[str, EngineProfile] = Field(default_factory=dict)

    _env_str = field_validator("env", mode="before")(_stringify)


class ResolvedRecipe(BaseModel):
    """One engine on one hardware profile, with overrides applied."""

    engine: str
    profile: str
    port: int
    image: Optional[str] = None
    model_dir: Optional[str] = None
    health_path: Optional[str] = None
    image_pull_policy: str = "IfNotPresent"
    cpu_request: Optional[str] = None
    cpu_burstable: bool = True
    env: Dict[str, str] = Field(default_factory=dict)


class RecipeFile(BaseModel):
    version: int
    default: EngineRecipe
    engines: Dict[str, EngineRecipe] = Field(default_factory=dict)


def _read(text: str) -> dict:
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _load() -> RecipeFile:
    builtin = _read(
        resources.files(__package__).joinpath(_BUILTIN).read_text(encoding="utf-8")
    )

    # Customer recipes replace a built-in engine wholesale rather than merging
    # field by field: a half-overridden engine is harder to reason about than
    # a replaced one, and the file is meant to be read on its own.
    override_dir = os.environ.get(_OVERRIDE_DIR_ENV)
    if override_dir and os.path.isdir(override_dir):
        for name in sorted(os.listdir(override_dir)):
            if not name.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(override_dir, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    extra = _read(fh.read())
            except OSError:
                logger.exception("recipes: could not read %s", path)
                continue
            for engine, recipe in (extra.get("engines") or {}).items():
                builtin.setdefault("engines", {})[engine] = recipe
                logger.info("recipes: %s overridden by %s", engine, name)

    return RecipeFile(**builtin)


_CACHE: Optional[RecipeFile] = None


def _recipes() -> RecipeFile:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load()
    return _CACHE


def reload() -> None:
    """Drop the cache. For tests, and for an override directory that changed."""
    global _CACHE
    _CACHE = None


def profile_for(gpu_allocated: int) -> str:
    return "gpu" if (gpu_allocated or 0) > 0 else "cpu"


def resolve(engine: Optional[str], profile: str) -> ResolvedRecipe:
    """The recipe for one engine on one profile.

    An unrecognised engine falls back to the default recipe with a warning.
    A recognised engine missing the requested profile raises, because that is
    a request for something real that we do not ship.
    """
    try:
        recipes = _recipes()
    except (ValidationError, yaml.YAMLError):
        logger.exception("recipes: could not load, falling back to defaults")
        return ResolvedRecipe(engine=str(engine or ""), profile=profile, port=8000)

    name = str(engine or "").lower()
    recipe = recipes.engines.get(name)

    if recipe is None:
        logger.warning(
            "recipes: no recipe for engine %r, using defaults. Known: %s",
            name, sorted(recipes.engines),
        )
        recipe = recipes.default
    elif profile not in recipe.profiles:
        raise UnknownProfile(
            f"engine {name!r} has no {profile!r} profile "
            f"(has: {sorted(recipe.profiles)})"
        )

    over = recipe.profiles.get(profile) or EngineProfile()

    def pick(field, default=None):
        for source in (over, recipe):
            value = getattr(source, field)
            if value is not None:
                return value
        return default

    return ResolvedRecipe(
        engine=name,
        profile=profile,
        port=pick("port", recipe.port),
        image=pick("image"),
        model_dir=pick("model_dir"),
        health_path=pick("health_path"),
        image_pull_policy=pick("image_pull_policy", "IfNotPresent"),
        cpu_request=pick("cpu_request"),
        cpu_burstable=pick("cpu_burstable", True),
        env={**recipe.env, **over.env},
    )


def known_engines() -> list:
    try:
        return sorted(_recipes().engines)
    except (ValidationError, yaml.YAMLError):
        return []
