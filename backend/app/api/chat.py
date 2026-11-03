from fastapi import APIRouter, HTTPException

from app.agent import chat
from app.models.chat import ChatExchange, ChatMessage, ChatRequest
from app.storage import chat as chat_storage
from app.storage import investigations as storage

router = APIRouter(prefix="/investigations", tags=["chat"])

_NOT_FOUND = {"code": "investigation_not_found", "message": "No investigation has this ID."}
_ERRORS = {
    chat.InvestigationNotFound: (404, _NOT_FOUND),
    chat.InvestigationRunning: (409, {"code": "investigation_running", "message": "Wait for the investigation to finish, then ask about it."}),
    chat.ChatBusy: (409, {"code": "chat_busy", "message": "A reply to an earlier message is still being prepared. Try again shortly."}),
    chat.ChatLimitReached: (409, {"code": "chat_limit_reached", "message": "This investigation's chat is full. Start a new investigation to continue."}),
    chat.BudgetExhausted: (429, {"code": "budget_exhausted", "message": "The local limit on AI requests was reached. Try again later."}),
}


@router.get("/{investigation_id}/chat", response_model=list[ChatMessage])
def get_chat(investigation_id: str) -> list[ChatMessage]:
    """The chat about one investigation, oldest first."""
    if storage.get_summary(investigation_id) is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return chat_storage.list_messages(investigation_id)


@router.post("/{investigation_id}/chat", response_model=ChatExchange)
def post_chat(investigation_id: str, request: ChatRequest) -> ChatExchange:
    """Ask about a finished investigation. The reply uses its saved evidence, and never changes it.

    Off-topic messages are answered without the LLM. When the LLM is unavailable, the reply says
    why and repeats the deterministic result. Suggested commands are advice: nothing is run.
    """
    try:
        return chat.reply(investigation_id, request.message)
    except tuple(_ERRORS) as exc:
        status, detail = _ERRORS[type(exc)]
        raise HTTPException(status_code=status, detail=detail) from None
