"""Capability-driven runtime for the project agent.

The legacy conversation interpreter remains available during migration.  New
agent runs use this package so intent, tool calls and durable execution are
separate concerns.
"""

from app.agent_runtime.contracts import (
    AgentDecision,
    AgentIntent,
    AgentRunState,
    Delegation,
    HardConstraint,
    Preference,
    ResolvedScope,
    ToolCall,
    ToolResult,
)
from app.agent_runtime.contracts import VerificationSummary


def __getattr__(name):
    # Importing a stable contract must not initialize geometry, storage or an
    # execution engine. Preserve public convenience exports lazily.
    modules = {"AgentEngine": "engine", "GatewayPolicy": "gateway", "ToolGateway": "gateway",
        "CapabilityRegistry": "registry", "AgentRunStore": "store", "verify_preview_data": "verifier"}
    if name not in modules:
        raise AttributeError(name)
    from importlib import import_module
    value = getattr(import_module(f"app.agent_runtime.{modules[name]}"), name)
    globals()[name] = value
    return value

__all__ = [
    "AgentDecision",
    "AgentEngine",
    "AgentIntent",
    "AgentRunState",
    "AgentRunStore",
    "CapabilityRegistry",
    "Delegation",
    "GatewayPolicy",
    "HardConstraint",
    "Preference",
    "ResolvedScope",
    "ToolCall",
    "ToolGateway",
    "ToolResult",
    "VerificationSummary",
    "verify_preview_data",
]
