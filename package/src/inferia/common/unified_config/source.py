"""Pydantic-Settings source that injects unified yaml values.

Lives between env and pydantic defaults in the precedence chain.
"""
from typing import Any, Tuple
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from .loader import load_unified_config


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Reads field values from the unified yaml for a given Settings subclass.

    The subclass declares which yaml sub-tree feeds it via the `_yaml_path`
    ClassVar (e.g. "services.api_gateway"). Shared sub-trees `security` and
    `infra` are auto-merged into every service's view, flattened with the
    rule documented in Section 6.1 of the spec.
    """

    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        self._yaml_path: str | None = getattr(settings_cls, "_yaml_path", None)
        self._values: dict[str, Any] = self._build_values()

    # --- internals -------------------------------------------------------
    def _walk(self, root: BaseModel, dotted: str) -> Any | None:
        node: Any = root
        for part in dotted.split("."):
            node = getattr(node, part, None)
            if node is None:
                return None
        return node

    def _flatten(self, node: BaseModel | None) -> dict[str, Any]:
        """Flatten one level of nested groups to Pydantic field names of settings_cls.

        For every `<group>.<leaf>` in `node`:
          - emit `<group>_<leaf>` if that is a declared field on settings_cls
          - else emit `<leaf>` if THAT is a declared field
          - else skip (silent — forward-compat or extra field)
        """
        if node is None:
            return {}
        declared = set(self.settings_cls.model_fields.keys())
        out: dict[str, Any] = {}
        for key, value in node.model_dump().items():
            if isinstance(value, dict):
                for leaf, leaf_val in value.items():
                    grouped = f"{key}_{leaf}"
                    if grouped in declared:
                        out[grouped] = leaf_val
                    elif leaf in declared:
                        out[leaf] = leaf_val
            else:
                if key in declared:
                    out[key] = value
        return out

    def _build_values(self) -> dict[str, Any]:
        cfg = load_unified_config()
        if cfg is None:
            return {}

        # Shared sub-tree merging is only enabled when the subclass declares a
        # _yaml_path AND that path resolves to an existing node in the yaml.
        # Classes without _yaml_path, or with a path that does not exist, get
        # no values from the yaml so they fall through to pydantic defaults.
        if self._yaml_path is None:
            return {}

        service_node = self._walk(cfg, self._yaml_path)
        if service_node is None:
            return {}

        # Shared sub-trees first; service-specific overlays on top.
        # Note: infra sub-tree was removed from yaml schema (hosting/URL → env only).
        merged: dict[str, Any] = {}
        merged.update(self._flatten(cfg.security))
        merged.update(self._flatten(service_node))

        # Top-level scalars
        declared = set(self.settings_cls.model_fields.keys())
        if "environment" in declared:
            merged["environment"] = cfg.environment
        if "log_level" in declared:
            merged["log_level"] = cfg.log_level
        return merged

    # --- pydantic-settings interface -------------------------------------
    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> Tuple[Any, str, bool]:
        if field_name in self._values:
            return self._values[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {
            name: self._values[name]
            for name in self.settings_cls.model_fields
            if name in self._values
        }
