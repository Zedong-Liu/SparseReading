"""Lightweight reading-episode leases for long, multi-task conversations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sparseread.core.benefit_gate import (
    BenefitDecision,
    CoverageShape,
    GateContext,
    GoalShape,
)

EpisodeStatus = Literal["open", "evidence_ready", "resolved"]


@dataclass(slots=True)
class ReadingEpisode:
    episode_id: str
    conversation_id: str
    turn_id: str
    scope: Path
    goal: GoalShape
    coverage: CoverageShape
    summary: str
    decision: BenefitDecision
    status: EpisodeStatus = "open"
    closure_ref: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "scope": str(self.scope),
            "goal": self.goal,
            "coverage": self.coverage,
            "summary": self.summary,
            "mode": self.decision.mode,
            "decision_code": self.decision.code,
            "status": self.status,
            "closure_ref": self.closure_ref,
        }


class EpisodeController:
    """Keep one active reading episode per conversation without session lock-in."""

    def __init__(self) -> None:
        self._current: dict[str, ReadingEpisode] = {}
        self._sequence = 0

    def bind(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        scope: str | Path,
        proposed: BenefitDecision,
        hint: GateContext | None = None,
    ) -> tuple[BenefitDecision, ReadingEpisode]:
        conversation = conversation_id or "default"
        normalized = Path(scope).resolve(strict=False)
        ctx = hint or GateContext()
        current = self._current.get(conversation)

        if current is not None:
            same_scope = self._related_scope(current.scope, normalized)
            goal_changed = ctx.goal != "unknown" and ctx.goal != current.goal
            coverage_changed = ctx.coverage != "unknown" and ctx.coverage != current.coverage
            explicit_switch = ctx.relation == "switch"
            # Legacy hosts may omit turn IDs. Treat two missing IDs as the same
            # lease so repeated calls within one task do not rotate forever.
            same_turn = turn_id == current.turn_id
            explicit_new = ctx.relation == "new" and (not same_turn or not same_scope)
            narrowed_discovery = (
                current.status == "open"
                and current.scope != normalized
                and current.scope in normalized.parents
                and current.decision.code == "collection_goal_required"
            )

            resume_resolved = current.status == "resolved" and same_scope and ctx.relation == "continue"
            if resume_resolved:
                current.turn_id = turn_id or current.turn_id
                current.summary = ctx.summary or current.summary
                if goal_changed:
                    current.goal = ctx.goal
                    current.decision = proposed
                if coverage_changed:
                    current.coverage = ctx.coverage
                    current.decision = proposed
                if goal_changed or coverage_changed:
                    current.status = "open"
                    current.closure_ref = ""
                else:
                    current.status = "evidence_ready" if current.closure_ref else "open"
                return current.decision, current

            if not explicit_switch and not explicit_new and not narrowed_discovery and same_scope and current.status != "resolved":
                if goal_changed or coverage_changed:
                    if goal_changed:
                        current.goal = ctx.goal
                    if coverage_changed:
                        current.coverage = ctx.coverage
                    current.summary = ctx.summary or current.summary
                    current.decision = proposed
                    current.status = "open"
                    current.closure_ref = ""
                current.turn_id = turn_id or current.turn_id
                return current.decision, current

            current.status = "resolved"

        episode = ReadingEpisode(
            episode_id=self._next_id(conversation, normalized),
            conversation_id=conversation,
            turn_id=turn_id,
            scope=normalized,
            goal=ctx.goal,
            coverage=ctx.coverage,
            summary=ctx.summary,
            decision=proposed,
        )
        self._current[conversation] = episode
        return proposed, episode

    def mark_ready(
        self,
        *,
        conversation_id: str,
        scope: str | Path,
        closure_ref: str,
    ) -> ReadingEpisode | None:
        current = self._current.get(conversation_id or "default")
        if current is None or not self._related_scope(current.scope, Path(scope).resolve(strict=False)):
            return None
        current.status = "evidence_ready"
        current.closure_ref = closure_ref
        return current

    def mark_output_started(self, conversation_id: str) -> ReadingEpisode | None:
        current = self._current.get(conversation_id or "default")
        if current is not None and current.status == "evidence_ready":
            current.status = "resolved"
        return current

    def mark_final(self, conversation_id: str) -> ReadingEpisode | None:
        current = self._current.get(conversation_id or "default")
        if current is not None:
            current.status = "resolved"
        return current

    def current(self, conversation_id: str) -> ReadingEpisode | None:
        return self._current.get(conversation_id or "default")

    def end(self, conversation_id: str) -> ReadingEpisode | None:
        episode = self._current.pop(conversation_id or "default", None)
        if episode is not None:
            episode.status = "resolved"
        return episode

    def trace(self) -> list[dict[str, object]]:
        return [episode.to_dict() for episode in self._current.values()]

    @staticmethod
    def _related_scope(left: Path, right: Path) -> bool:
        return left == right or left in right.parents or right in left.parents

    def _next_id(self, conversation: str, scope: Path) -> str:
        self._sequence += 1
        digest = hashlib.sha1(f"{conversation}\0{scope}\0{self._sequence}".encode()).hexdigest()[:10]
        return f"sro_episode_{digest}"
