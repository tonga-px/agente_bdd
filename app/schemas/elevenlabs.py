from pydantic import BaseModel


class OutboundCallResponse(BaseModel):
    success: bool = False
    message: str | None = None
    conversation_id: str | None = None
    sip_call_id: str | None = None


class ConversationTranscriptEntry(BaseModel):
    role: str  # "agent" | "user"
    message: str | None = ""


class ConversationAnalysis(BaseModel):
    extracted_data: dict = {}
    data_collection_results: dict = {}


class ConversationResponse(BaseModel):
    conversation_id: str = ""
    status: str = ""  # initiated | in-progress | processing | done | failed
    transcript: list[ConversationTranscriptEntry] = []
    analysis: ConversationAnalysis | None = None
    metadata: dict | None = None
