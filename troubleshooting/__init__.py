"""Interactive troubleshooting tree — shared loader + schema.

``docs/troubleshooting/tree.yaml`` is the single source of truth for the operator
troubleshooting tree. It drives BOTH:

  * the printable guide (``scripts/build_troubleshooting_guide.py`` → a manifest-registered
    branded PDF), and
  * the interactive dashboard view (``operator_dashboard`` ``/troubleshoot``, added in a
    later tranche).

This package is the shared, network-free loader/validator both consumers import so the two
renderings can never drift from one schema. See ``docs/troubleshooting/schema.md`` for the
authored spec and the class-assignment rule.
"""
from __future__ import annotations

from .loader import (
    CLASSES,
    FailureMode,
    Step,
    Tree,
    TreeError,
    WhatHappens,
    Workflow,
    load_tree,
    tree_path,
)

__all__ = [
    "CLASSES",
    "FailureMode",
    "Step",
    "Tree",
    "TreeError",
    "WhatHappens",
    "Workflow",
    "load_tree",
    "tree_path",
]
