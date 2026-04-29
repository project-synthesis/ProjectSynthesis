"""MCP tool handlers — extracted from mcp_server.py for maintainability.

Copyright 2025-2026 Project Synthesis contributors.
"""

from app.tools.analyze import handle_analyze
from app.tools.delete import handle_delete
from app.tools.explain import handle_explain
from app.tools.feedback import handle_feedback
from app.tools.get_optimization import handle_get_optimization
from app.tools.health import handle_health
from app.tools.history import handle_history
from app.tools.match import handle_match
from app.tools.optimize import handle_optimize
from app.tools.prepare import handle_prepare
from app.tools.probe import handle_probe
from app.tools.refine import handle_refine
from app.tools.save_result import handle_save_result
from app.tools.seed import handle_seed
from app.tools.strategies import handle_strategies

__all__ = [
    "handle_analyze",
    "handle_delete",
    "handle_explain",
    "handle_feedback",
    "handle_get_optimization",
    "handle_health",
    "handle_history",
    "handle_match",
    "handle_optimize",
    "handle_prepare",
    "handle_probe",
    "handle_refine",
    "handle_save_result",
    "handle_seed",
    "handle_strategies",
]
