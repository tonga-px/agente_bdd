from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    hubspot_access_token: str
    google_places_api_key: str
    tripadvisor_api_key: str = ""
    overwrite_existing: bool = False
    log_level: str = "INFO"
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_phone_number_id: str = ""
