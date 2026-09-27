"""Shared candidate vocabulary used by rule results and transport schemas."""

from typing import Literal

OperationType = Literal["add", "update", "delete"]
RejectedStatus = Literal["blocked", "soft_conflict", "unknown"]
CandidateStatus = Literal["allowed", "blocked", "soft_conflict", "unknown"]
RejectedCategory = Literal["constraint", "growth", "data", "spacing", "operation"]
CandidateCategory = Literal[
    "accepted", "constraint", "growth", "data", "spacing", "operation"
]
