"""Shared test fixture builders.

Sub-modules expose pure-Python builder functions returning realistic
upstream-provider payload shapes (DataFrames, JSON dicts, ZIP bytes).

These are imported directly by tests; they are not pytest fixtures because
the same builders are used across both unit and integration tests with
slightly different wiring.
"""
