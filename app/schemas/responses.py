from datetime import datetime

from pydantic import BaseModel


class FieldChange(BaseModel):
    field: str
    old_value: str | None
    new_value: str | None


class CompanyResult(BaseModel):
    company_id: str
    company_name: str | None
    status: str  # "enriched" | "no_results" | "error"
    message: str | None = None
    changes: list[FieldChange] = []
    note: str | None = None


class EnrichmentResponse(BaseModel):
    total_found: int
    enriched: int
    no_results: int
    errors: int
    results: list[CompanyResult]


class JobSubmittedResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    company_id: str | None = None
    result: EnrichmentResponse | None = None
    error: str | None = None
