"""Compatibility import for existing preview workers and receipts."""

from app.cad_intake.work import CadWork as PreviewWork

__all__ = ["PreviewWork"]
