"""Workflow failures expressed independently of an HTTP transport."""


class WorkflowConflict(Exception):
    """The user's command no longer matches the current durable workflow."""
