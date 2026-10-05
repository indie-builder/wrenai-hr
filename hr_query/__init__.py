"""Shared SQL validation and read-only DuckDB execution.

The package itself stays dependency-free; callers import the policy or worker
module they need. Planning and process lifecycles belong to their callers.
"""
