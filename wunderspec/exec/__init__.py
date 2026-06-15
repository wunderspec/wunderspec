"""
Execution context package for running state machine actions.

This package provides:
- ExecContext: An execution context for running state machine actions
- ScriptedScheduler: A scheduler that replays scripted decisions
- RandomScheduler: A scheduler that generates random decisions
- Various helper classes for tracking branching and state checkpoints
"""

from .action_exec import action_execute, state_view
from .context import (
    AssumptionViolated,
    BranchingTracker,
    ExecAlternative,
    ExecContext,
    StateCheckpoint,
    assumption_interpreter,
)
from .scheduler import (
    RandomScheduler,
    RecordingScheduler,
    Scheduler,
    SchedulerAlternative,
    SchedulerChoiceIndex,
    SchedulerDecision,
    SchedulerRawValue,
    SchedulerRequest,
    SchedulerRequestAlternative,
    SchedulerRequestOneOf,
    SchedulerRequestSplit,
    SchedulerSplit,
    SchedulerValue,
    ScriptedScheduler,
)

__all__ = [
    # Action execution
    "action_execute",
    "state_view",
    # Context
    "ExecContext",
    "AssumptionViolated",
    "assumption_interpreter",
    "BranchingTracker",
    "StateCheckpoint",
    "ExecAlternative",
    # Scheduler
    "Scheduler",
    "ScriptedScheduler",
    "RandomScheduler",
    "RecordingScheduler",
    "SchedulerRequest",
    "SchedulerRequestOneOf",
    "SchedulerRequestAlternative",
    "SchedulerRequestSplit",
    "SchedulerDecision",
    "SchedulerValue",
    "SchedulerRawValue",
    "SchedulerAlternative",
    "SchedulerChoiceIndex",
    "SchedulerSplit",
]
