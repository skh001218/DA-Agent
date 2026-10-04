"""Versioned contracts for the first request-based training capability."""
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Contract

Kind = Literal['auto', 'calculation', 'review', 'design']
Difficulty = Literal['auto', 'beginner', 'intermediate', 'advanced']


class TrainingRequest(Contract):
    request_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)
    difficulty: Difficulty = 'auto'
    task_kind: Kind = 'auto'


class Conversation(Contract):
    action_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)
    trigger: Literal['question', 'sql_result', 'report'] = 'question'
    execution_id: str | None = None
    saved_execution_id: str | None = None
    transient: bool = False


class PublicTask(Contract):
    package_id: str
    release_version: str
    dataset_id: str
    problem_id: str
    problem_type_id: str
    problem_version: str
    title: str
    description: str
    timezone: str
    required_tables: list[Literal['users', 'sessions']]
    cohort_start: str | None = None
    cohort_end: str | None = None
    data_complete_before: str
    definitions: dict[str, str] | None = None
    task_kind: Literal['calculation', 'review', 'design']
    difficulty: Literal['beginner', 'intermediate', 'advanced']
    completion_conditions: list[str] = Field(min_length=1)
    weights: dict[Literal['problem_definition', 'analysis_approach', 'sql_accuracy', 'interpretation', 'next_actions'], int]
    selection_reason: str
    difficulty_reason: str
    contract_version: Literal['request-v1']
    plan_version: Literal['access-plan-v1']
    evaluation_version: Literal['request-review-v1']
    difficulty_version: Literal['ambiguity-v1']
    data_preparation: str
    evaluation_status: str
    analysis_draft: str | None = None

    @model_validator(mode='after')
    def validate_weights(self):
        if sum(self.weights.values()) != 100 or any(type(v) is not int or v <= 0 for v in self.weights.values()):
            raise ValueError('Invalid weights')
        if self.task_kind == 'design' and 'sql_accuracy' in self.weights:
            raise ValueError('Design must not require SQL')
        return self


class OperatorEvent(Contract):
    event_id: str
    event_type: Literal['request_state', 'ai_started', 'ai_finished']
    schema_version: Literal['training-events-v1'] = 'training-events-v1'
    occurred_at: str
    operation_id: str
    parent_operation_id: str | None = None
    request_id: str | None = None
    attempt_id: str | None = None
    status: str
    error_code: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    model: str | None = None
    usage: dict[Literal['input_tokens', 'output_tokens', 'total_tokens'], int | None] | None = None
    usage_missing_reason: str | None = None
    task_kind: str | None = None
    difficulty: str | None = None
    rules_version: str | None = None
