"""Strict YAML loading and build-time MDL compilation for this schema-v5 project.

The compiler is intentionally limited to the directory layout used here. CI
compares its result with Wren's compiler; runtime workers consume the built JSON
and do not import PyYAML or the CLI.
"""
from pathlib import Path

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError as exc:
                raise ValueError(f"YAML键无效，行{key_node.start_mark.line + 1}") from exc
            if duplicate:
                raise ValueError(f"YAML重复键，行{key_node.start_mark.line + 1}")
        return super().construct_mapping(node, deep=deep)


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, UniqueKeyLoader.construct_mapping,
)


def load_yaml(path):
    return yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


def _camel(key):
    prefix = key[:len(key) - len(key.lstrip("_"))]
    first, *rest = key.lstrip("_").split("_")
    return prefix + first + "".join(part.capitalize() for part in rest)


def _engine_keys(value):
    if isinstance(value, dict):
        return {_camel(key): _engine_keys(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_engine_keys(child) for child in value]
    return value


def build_mdl(project):
    """Return the engine manifest from canonical YAML, never a cached target."""
    project = Path(project)
    config = load_yaml(project / "wren_project.yml")
    if str(config.get("schema_version")) != "5":
        raise ValueError("轻量构建仅支持 schema_version: 5；请同步编译器与 Wren 构建检查。")
    manifest = {"catalog": config.get("catalog", "wren"), "schema": config.get("schema", "public"),
                "data_source": config["data_source"], "layoutVersion": 3}
    for category in ("models", "views", "cubes"):
        manifest[category] = []
        for path in sorted((project / category).glob("*/metadata.yml")):
            item = load_yaml(path)
            if not isinstance(item, dict) or item.get("name") != path.parent.name:
                raise ValueError(f"{path}: 定义名称与目录不一致")
            if category == "models" and (path.parent / "ref_sql.sql").exists():
                statement = (path.parent / "ref_sql.sql").read_text(encoding="utf-8").strip()
                if statement:
                    item["ref_sql"] = statement
            if category == "views" and (path.parent / "sql.yml").exists():
                sql = load_yaml(path.parent / "sql.yml")
                if isinstance(sql, dict) and sql.get("statement"):
                    item["statement"] = sql["statement"]
            list_fields = {"models": ("columns",), "cubes": ("measures", "dimensions", "time_dimensions")}
            for field in list_fields.get(category, ()):
                if field in item:
                    item[field] = item[field] or []
            manifest[category].append(item)
    path = project / "relationships.yml"
    relationships = (load_yaml(path) or {}) if path.exists() else {}
    manifest["relationships"] = relationships.get("relationships") or []
    return _engine_keys(manifest)
