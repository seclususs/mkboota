from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mkboota.models import LoopMode


class DurationCategory(StrEnum):
    SHORT = "short"
    STANDARD = "standard"
    LONG = "long"


@dataclass(frozen=True, slots=True)
class DurationAdvice:
    category: DurationCategory
    duration: float
    recommended_loop_mode: LoopMode
    message: str


class DurationAdvisor:
    def evaluate(self, duration: float) -> DurationAdvice:
        if duration < 0:
            raise ValueError(f"Duration must be non-negative, got {duration}")

        if duration < 5.0:
            return DurationAdvice(
                category=DurationCategory.SHORT,
                duration=duration,
                recommended_loop_mode=LoopMode.LOOP,
                message=f"Short duration ({duration:.1f}s, <5.0s); LoopMode.LOOP recommended",
            )

        if duration <= 15.0:
            return DurationAdvice(
                category=DurationCategory.STANDARD,
                duration=duration,
                recommended_loop_mode=LoopMode.LOOP,
                message=f"Standard duration ({duration:.1f}s); LoopMode.LOOP recommended",
            )

        # Videos > 15s risk SurfaceFlinger texture memory exhaustion if looped continuously;
        # HOLD_LAST keeps only the final frame in memory once played
        return DurationAdvice(
            category=DurationCategory.LONG,
            duration=duration,
            recommended_loop_mode=LoopMode.HOLD_LAST,
            message=f"Long duration ({duration:.1f}s, >15.0s); LoopMode.HOLD_LAST recommended",
        )


__all__ = ["DurationAdvice", "DurationAdvisor", "DurationCategory"]
