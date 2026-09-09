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
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.gateway import GatewayPolicy, ToolGateway
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.store import AgentRunStore
from app.agent_runtime.verifier import VerificationSummary, verify_preview_data

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
