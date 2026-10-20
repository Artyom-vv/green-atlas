"""Лимиты времени, памяти, входа и выхода прежней CAD-конвертации для каждого отдельного
чертежа.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversionPolicy:
    # Бюджет действует на чертёж, а не архив; рост результата отслеживается при выполнении.
    timeout_seconds: float = 120
    max_output_bytes: int = 512 * 1024 * 1024
    max_source_bytes: int = 128 * 1024 * 1024
    max_log_bytes: int = 4 * 1024 * 1024
    # None сохраняет измерение памяти, но не останавливает процесс по её объёму.
    max_memory_bytes: int | None = 2 * 1024 * 1024 * 1024
    poll_seconds: float = 0.1

    def __post_init__(self) -> None:
        if (
            min(
                self.timeout_seconds,
                self.max_output_bytes,
                self.max_source_bytes,
                self.max_log_bytes,
                self.poll_seconds,
            )
            <= 0
        ):
            raise ValueError("CAD conversion budgets must be positive")
        if self.max_memory_bytes is not None and self.max_memory_bytes <= 0:
            raise ValueError("CAD conversion memory budget must be positive")
