"""RunGenerator protocol + GeneratorResult service-layer dataclass."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.schemas.runs import RunRequest


@dataclass(frozen=True)
class GeneratorResult:
    """Service-layer dataclass — what generators return to RunOrchestrator.

    Distinct from `app.schemas.runs.RunResult` (Pydantic response model).
    """
    terminal_status: Literal["completed", "partial", "failed"]
    prompts_generated: int
    prompt_results: list[dict]
    aggregate: dict
    taxonomy_delta: dict
    final_report: str | None


@runtime_checkable
class RunGenerator(Protocol):
    """Awaitable mode-specific run executor.

    Generators MUST publish progress events directly to event_bus with run_id
    in payload. They MUST NOT touch RunRow — RunOrchestrator owns row writes.
    """

    async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult:
        ...
