from __future__ import annotations

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


class ExtractedCallData(BaseModel):
    hotel_name: str | None = None
    num_rooms: str | None = None
    decision_maker_name: str | None = None
    decision_maker_phone: str | None = None
    decision_maker_email: str | None = None
    date_and_time: str | None = None


class CallAttempt(BaseModel):
    phone_number: str
    source: str  # "company" | "contact:{id}:{phone|mobile}"
    conversation_id: str | None = None
    status: str  # "connected" | "no_answer" | "failed" | "error"
    error: str | None = None


class ProspeccionResponse(BaseModel):
    company_id: str
    company_name: str | None = None
    status: str  # "completed" | "no_phone" | "all_failed" | "error"
    message: str | None = None
    call_attempts: list[CallAttempt] = []
    extracted_data: ExtractedCallData | None = None
    transcript: str | None = None
    note: str | None = None


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
    result: EnrichmentResponse | ProspeccionResponse | None = None
    error: str | None = None
