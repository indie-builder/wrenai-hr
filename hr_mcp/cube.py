"""Validate the bounded public subset of Core's CubeQuery."""
from __future__ import annotations

import copy
import math

from hr_mcp.contracts import MAX_FILTER_VALUES, MAX_MEMBERS, fail


def _selection(value, allowed, *, required=False):
    if (not isinstance(value, list) or len(value) > MAX_MEMBERS
            or (required and not value) or not all(isinstance(item, str) for item in value)
            or len(set(value)) != len(value) or not set(value) <= allowed):
        fail("INVALID_ARGUMENT")
    return list(value)


def validate_cube_request(mdl, cube, measures, dimensions, filters=None):
    cubes = {item["name"]: item for item in mdl.get("cubes", [])}
    if not isinstance(cube, str) or cube not in cubes:
        fail("CUBE_NOT_FOUND")
    definition = cubes[cube]
    measure_names = {item["name"] for item in definition.get("measures", [])}
    dimension_names = {item["name"] for item in definition.get("dimensions", [])}
    filter_names = dimension_names | {item["name"] for item in definition.get("timeDimensions", [])}
    request = {
        "cube": cube,
        "measures": _selection(measures, measure_names, required=True),
        "dimensions": _selection(dimensions, dimension_names),
    }
    if filters is None:
        filters = []
    if not isinstance(filters, list) or len(filters) > MAX_MEMBERS:
        fail("INVALID_ARGUMENT")
    operators = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",
                 "contains", "starts_with", "is_null", "is_not_null"}

    def scalar(value):
        return ((isinstance(value, str) and len(value) <= 1000)
                or isinstance(value, bool)
                or (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and abs(value) <= 10**18 and math.isfinite(value)))

    checked = []
    for item in filters:
        if (not isinstance(item, dict) or set(item) - {"dimension", "operator", "value"}
                or not isinstance(item.get("dimension"), str) or item["dimension"] not in filter_names
                or not isinstance(item.get("operator"), str) or item["operator"] not in operators):
            fail("INVALID_ARGUMENT")
        operator, value = item["operator"], item.get("value")
        if operator in {"is_null", "is_not_null"}:
            if value is not None:
                fail("INVALID_ARGUMENT")
            checked.append({"dimension": item["dimension"], "operator": operator})
            continue
        if operator in {"in", "not_in"}:
            if (not isinstance(value, list) or not 1 <= len(value) <= MAX_FILTER_VALUES
                    or not all(scalar(entry) for entry in value)):
                fail("INVALID_ARGUMENT")
        elif not scalar(value) or (operator in {"contains", "starts_with"} and not isinstance(value, str)):
            fail("INVALID_ARGUMENT")
        checked.append(copy.deepcopy(item))
    if checked:
        request["filters"] = checked
    return request
