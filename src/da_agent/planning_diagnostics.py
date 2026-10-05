"""Closed diagnostic vocabulary: never store provider text, values or exceptions."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class InterpretationIssue(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    field: Literal['output', 'unknown_field', 'analysis_topic', 'capability_id', 'difficulty', 'task_kind', 'goal', 'questions', 'unsupported', 'reason']
    kind: Literal['missing', 'extra', 'type', 'enum', 'length', 'constraint', 'unknown_capability', 'capability_kind_mismatch', 'invalid_json', 'unexpected']

class InterpretationDiagnostic(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal['interpretation-diagnostics-v1'] = 'interpretation-diagnostics-v1'
    stage: Literal['provider', 'json', 'schema', 'selection', 'internal']
    issues: list[InterpretationIssue] = Field(default_factory=list, max_length=8)
    provider_reason: Literal['api_key_missing', 'api_key_invalid', 'api_permission_denied', 'model_unavailable', 'api_rate_limited', 'api_unavailable', 'api_request_invalid', 'api_content_blocked', 'api_empty_response', 'response_incomplete', 'api_timeout', 'api_invalid_response', 'unknown'] | None = None

def diagnostic(stage, issues=(), provider_reason=None):
    return InterpretationDiagnostic(stage=stage, issues=list(issues), provider_reason=provider_reason).model_dump(exclude_none=True)
