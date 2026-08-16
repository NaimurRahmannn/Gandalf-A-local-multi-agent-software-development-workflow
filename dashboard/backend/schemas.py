"""Validated API request bodies."""

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PhaseStart(BaseModel):
    project_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=20_000)


class ApprovalInput(BaseModel):
    feedback: str = Field(default="", max_length=20_000)
