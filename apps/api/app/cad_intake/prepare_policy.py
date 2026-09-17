"""Agreed first-stage resource budget; ordinary upload limits are unchanged."""

from app.cad_import.policy import ConversionPolicy

PREPARE_POLICY = ConversionPolicy(
    timeout_seconds=300,
    max_memory_bytes=4 * 1024**3,
    max_source_bytes=512 * 1024**2,
    max_output_bytes=512 * 1024**2,
)
MAX_PREPARE_RECEIPT_BYTES = 64 * 1024
