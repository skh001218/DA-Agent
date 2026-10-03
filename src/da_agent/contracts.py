from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Start(Contract):
    package_id: str = Field(max_length=100)
    release_version: str = Field(max_length=100)
    problem_id: str = Field(max_length=100)


class Draft(Contract):
    revision: int = Field(ge=0)
    sections: dict[str, str]


class Execute(Contract):
    sql: str = Field(min_length=1, max_length=20000)


class Save(Contract):
    execution_id: str
    request_id: str = Field(min_length=1, max_length=100)


class Evidence(Contract):
    saved_execution_id: str
    rows: list[int] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)


class Claim(Contract):
    claim_id: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=20000)
    evidence_refs: list[Evidence] = Field(default_factory=list, max_length=20)


class Report(Contract):
    revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=100)
    previous_report_id: str | None = None
    content: dict[str, str]
    claims: list[Claim] = Field(default_factory=list, max_length=50)


class ReviewRequest(Contract):
    request_id: str = Field(min_length=1, max_length=100)


class Hint(Contract):
    level: Literal["direction", "metric", "sql_structure"]


class Coach(Contract):
    message: str = Field(min_length=1, max_length=20000)
    saved_execution_id: str | None = None
