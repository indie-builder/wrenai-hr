#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查或导出 v2 扩展的 14 个模型；models/*/metadata.yml 是唯一规范源。

默认仅校验源文件，不再用脚本中另一份字段定义覆盖已验证的模型。
使用 --output-dir <目录> 可向独立目录导出相同 YAML；修改类型、计算列和
描述时直接编辑 models/，随后按项目流程 validate/build/index。
需使用仓库 .venv 中已随 Wren 安装的 PyYAML。
"""

import argparse
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError


MODEL_NAMES = (
    "headcount_plan", "promotions", "contracts", "salary_changes",
    "insurance_payments", "awards_penalties", "overtime_requests",
    "leave_balances", "offers", "recruitment_costs", "performance_goals",
    "talent_pool", "engagement_surveys", "exit_interviews",
)
SOURCE_DIR = Path(__file__).resolve().parent / "models"


class UniqueKeyLoader(yaml.SafeLoader):
    """拒绝重复键，避免 YAML 静默用后一项覆盖字段类型或属性。"""


def unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"duplicate key: {key}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping,
)


def canonical_models():
    """先完整校验所有模型，再允许导出，避免留下部分错误产物。"""
    documents = {}
    for name in MODEL_NAMES:
        path = SOURCE_DIR / name / "metadata.yml"
        text = path.read_text(encoding="utf-8")
        model = yaml.load(text, Loader=UniqueKeyLoader)
        if not isinstance(model, dict) or model.get("name") != name:
            raise ValueError(f"{path}: 模型名称与目录不一致")
        columns = model.get("columns", [])
        names = [column["name"] for column in columns]
        if not names or len(names) != len(set(names)):
            raise ValueError(f"{path}: 列定义为空或列名重复")
        if model.get("primary_key") not in names:
            raise ValueError(f"{path}: 主键不在列定义中")
        documents[name] = text
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        help="可选导出目录；省略时只校验，不改写规范源",
    )
    args = parser.parse_args()
    output = args.output_dir.resolve() if args.output_dir else None
    if output is not None and (output == SOURCE_DIR or SOURCE_DIR in output.parents):
        parser.error("导出目录必须位于规范 models/ 目录之外")
    try:
        documents = canonical_models()
        if output is not None:
            for name, text in documents.items():
                path = output / name / "metadata.yml"
                if path.resolve().is_relative_to(SOURCE_DIR):
                    raise ValueError(f"导出路径不能覆盖规范源: {path}")
            for name, text in documents.items():
                path = output / name / "metadata.yml"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        parser.error(str(exc))
    action = "已校验并导出" if output is not None else "已校验（未改写）"
    print(f"{action} {len(documents)} 个规范模型")


if __name__ == "__main__":
    main()
