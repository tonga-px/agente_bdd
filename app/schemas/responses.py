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


class EnrichmentResponse(BaseModel):
    total_found: int
    enriched: int
    no_results: int
    errors: int
    results: list[CompanyResult]
