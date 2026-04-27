"""Agent state machine: phase tracking, step management, facts persistence.

The model does not remember progress — the CLI does.  Every model call
receives a current-state summary injected into the prompt instead of
relying on conversation history.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import config


class Phase(Enum):
    IDLE = "IDLE"
    EXPLORING = "EXPLORING"   # reading files, understanding codebase
    PLANNING = "PLANNING"     # generating execution plan (step list)
    PATCHING = "PATCHING"     # writing files, making changes
    VERIFYING = "VERIFYING"   # running tests, checking results
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass
class AgentStep:
    id: str
    intent: str
    target_files: list[str] = field(default_factory=list)
    status: str = "pending"            # pending | running | done | failed
    result: str | None = None
    error: str | None = None
    retry_count: int = 0


@dataclass
class Facts:
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)


@dataclass
class AgentState:
    task_id: str
    task_description: str
    phase: Phase = Phase.IDLE
    steps: list[AgentStep] = field(default_factory=list)
    current_step_index: int = 0
    facts: Facts = field(default_factory=Facts)
    iteration_count: int = 0
    max_iterations: int = 20
    snapshot_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = int(time.time())
        self.created_at = self.created_at or str(now)
        self.updated_at = str(now)

    def current_step(self) -> AgentStep | None:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def all_steps_done(self) -> bool:
        return len(self.steps) > 0 and all(s.status == "done" for s in self.steps)


class StateManager:
    """Load / save AgentState as JSON under data/agent_state/."""

    def __init__(self, task_id: str):
        self._dir = config.data_dir() / "agent_state"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{task_id}.json"

    def load(self) -> AgentState | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text("utf-8"))
            return _from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None

    def save(self, state: AgentState) -> None:
        state.updated_at = str(int(time.time()))
        self._path.write_text(json.dumps(_to_dict(state), ensure_ascii=False, indent=2), "utf-8")

    def to_prompt_context(self, state: AgentState) -> str:
        step = state.current_step()
        lines = [
            "## Task State",
            f"- task: {state.task_description}",
            f"- phase: {state.phase.value}",
            f"- step ({state.current_step_index + 1}/{len(state.steps)}): {step.intent if step else 'none'}",
            f"- iteration: {state.iteration_count}/{state.max_iterations}",
            f"- files read: {', '.join(state.facts.files_read) or 'none'}",
            f"- files modified: {', '.join(state.facts.files_modified) or 'none'}",
        ]
        if state.facts.key_findings:
            lines.append("- key findings:")
            for f in state.facts.key_findings:
                lines.append(f"  - {f}")
        if state.facts.constraints:
            lines.append("- constraints:")
            for c in state.facts.constraints:
                lines.append(f"  - {c}")
        return "\n".join(lines)


def new_task_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# serialization helpers
# ---------------------------------------------------------------------------

def _to_dict(state: AgentState) -> dict:
    return {
        "task_id": state.task_id,
        "task_description": state.task_description,
        "phase": state.phase.value,
        "steps": [
            {
                "id": s.id, "intent": s.intent, "target_files": s.target_files,
                "status": s.status, "result": s.result, "error": s.error,
                "retry_count": s.retry_count,
            }
            for s in state.steps
        ],
        "current_step_index": state.current_step_index,
        "facts": {
            "files_read": state.facts.files_read,
            "files_modified": state.facts.files_modified,
            "key_findings": state.facts.key_findings,
            "constraints": state.facts.constraints,
        },
        "iteration_count": state.iteration_count,
        "max_iterations": state.max_iterations,
        "snapshot_id": state.snapshot_id,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def _from_dict(data: dict) -> AgentState:
    facts_data = data.get("facts", {})
    return AgentState(
        task_id=data["task_id"],
        task_description=data.get("task_description", ""),
        phase=Phase(data.get("phase", "IDLE")),
        steps=[AgentStep(**s) for s in data.get("steps", [])],
        current_step_index=data.get("current_step_index", 0),
        facts=Facts(
            files_read=facts_data.get("files_read", []),
            files_modified=facts_data.get("files_modified", []),
            key_findings=facts_data.get("key_findings", []),
            constraints=facts_data.get("constraints", []),
        ),
        iteration_count=data.get("iteration_count", 0),
        max_iterations=data.get("max_iterations", 20),
        snapshot_id=data.get("snapshot_id"),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )
