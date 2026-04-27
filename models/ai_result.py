"""
Pydantic schema for the JSON object returned by Gemini analyze_notice prompts.

The AI is instructed via resources/prompts/system_prompt.txt to return
specific keys, but in practice the model occasionally:
- emits "미정"/"없음"/"" for date fields where null is expected
- omits optional fields entirely
- returns target_grades as strings instead of ints

AIAnalysisResult applies normalizing validators so callers downstream
always see well-typed values without scattering defensive checks.
"""
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


_NULL_SENTINELS = {"미정", "없음", "없다", "null", "none", "n/a", "-", ""}


def _normalize_optional_str(value):
    """Treat AI sentinels for 'unknown' as None."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().lower() in _NULL_SENTINELS:
            return None
        return value.strip()
    return value


class AIAnalysisResult(BaseModel):
    """Validated result of AIService.analyze_notice."""

    summary: str = ""
    category: str = "일반"
    tags: List[str] = Field(default_factory=list)
    target_grades: List[int] = Field(default_factory=list)
    target_dept: Optional[str] = None
    deadline: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    eligibility: List[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @field_validator("summary", "category", mode="before")
    @classmethod
    def _coerce_str(cls, value):
        if value is None:
            return ""
        return str(value)

    @field_validator("tags", "eligibility", mode="before")
    @classmethod
    def _coerce_str_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return [str(v) for v in value if v is not None and str(v).strip()]

    @field_validator("target_grades", mode="before")
    @classmethod
    def _coerce_int_list(cls, value):
        if value is None:
            return []
        result = []
        for v in value:
            try:
                result.append(int(v))
            except (TypeError, ValueError):
                continue
        return result

    @field_validator("target_dept", "deadline", "start_date", "end_date", mode="before")
    @classmethod
    def _normalize_optional(cls, value):
        return _normalize_optional_str(value)
