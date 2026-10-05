"""Separate request-v2 contracts; v1 records retain their original meaning."""
from typing import Literal
from pydantic import ConfigDict, Field, model_validator
from .contracts import Contract
from .training_contracts import PublicTask

class RequestV2(Contract):
    contract_version: Literal['request-v2']
    request_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)
    difficulty: Literal['auto','beginner','intermediate','advanced'] = 'auto'
    task_kind: Literal['auto','calculation','review','design','investigation'] = 'auto'
    domain: str = Field(default='auto', max_length=100)
    goal: str | None = Field(default=None, max_length=200)
    sql_level: str | None = Field(default=None, max_length=100)
    time_condition: str | None = Field(default=None, max_length=200)
    intentional_repeat: bool = False
    recommendation_id: str | None = Field(default=None, max_length=100)
    data_mode: Literal['adaptive','generated','existing'] = 'adaptive'
    user_count: int = Field(default=200, ge=50, le=1000)

class RequestAction(Contract):
    action_id: str = Field(min_length=1,max_length=100)
    expected_revision: int = Field(ge=0)
    message: str | None = Field(default=None,max_length=4000)
    difficulty: Literal['beginner','intermediate','advanced'] | None = None
    task_kind: Literal['calculation','review','design','investigation'] | None = None

class Interpretation(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)
    analysis_topic: Literal['return_observation', 'unsupported', 'unclear']
    capability_id: str | None
    difficulty: Literal['beginner','intermediate','advanced']
    task_kind: Literal['calculation','review','design','investigation']
    goal: str = Field(min_length=1,max_length=200)
    questions: list[str] = Field(max_length=5)
    unsupported: bool
    reason: str = Field(min_length=1,max_length=1000)

class PublicTaskV2(PublicTask):
    original_request: str | None = Field(default=None,max_length=4000)
    required_tables: list[str] = Field(min_length=1, max_length=4)
    task_kind: Literal['calculation','review','design','investigation']
    contract_version: Literal['request-v2']
    plan_version: Literal['access-plan-v2', 'adaptive-plan-v1']
    evaluation_version: Literal['request-review-v2', 'request-review-v3']
    evaluation_rubric: dict | None = None
    difficulty_version: Literal['ambiguity-v2']
    plan_id: str
    revision: int
    capability_id: str
    goal: str
    semantic_signature: dict[str,str]
    evaluation_rules_version: str
    generator_version: str
    validation_version: str
    ambiguity: dict[str,str]
    required_judgments: list[str]
    allowed_limits: list[str]
    disclosed_on_question: list[str]

class RuleApproval(Contract):
    reviewer: str = Field(min_length=1,max_length=100)
    sample_ids: list[str] = Field(default_factory=list,max_length=100)
    rules_version: Literal['access-rules-v2']
    result: Literal['approved','disabled']

class RuleSample(Contract):
    difficulty: Literal['beginner','intermediate','advanced'] = 'intermediate'
    task_kind: Literal['calculation','review','design','investigation']
    user_count: int = Field(default=200,ge=50,le=1000)
