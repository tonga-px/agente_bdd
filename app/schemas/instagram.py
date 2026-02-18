from pydantic import BaseModel


class InstagramData(BaseModel):
    username: str | None = None
    full_name: str | None = None
    biography: str | None = None
    profile_url: str | None = None
    external_url: str | None = None
    follower_count: int | None = None
    business_email: str | None = None
    business_phone: str | None = None  # E.164
    bio_phones: list[str] = []  # E.164
    bio_emails: list[str] = []
    whatsapp: str | None = None  # E.164
