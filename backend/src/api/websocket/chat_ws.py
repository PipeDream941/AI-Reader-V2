"""WebSocket endpoint for streaming chat Q&A."""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.services.query_service import query_stream

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/chat/{session_id}")
async def chat_ws(websocket: WebSocket, session_id: str):
    """
    WebSocket chat endpoint.

    Client sends JSON: {
      "novel_id": str,
      "question": str,
      "conversation_id": str | null,
      "model": str | null,
      "reasoning_effort": str | null
    }
    Server streams JSON messages:
      {"type": "profile", "model": str, "reasoning_effort": str}
      {"type": "token", "content": str}
      {"type": "sources", "chapters": [int]}
      {"type": "done"}
      {"type": "error", "message": str}
    """
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "message": "Invalid JSON"}
                )
                continue

            novel_id = msg.get("novel_id")
            question = msg.get("question", "").strip()
            conversation_id = msg.get("conversation_id")
            model = msg.get("model")
            reasoning_effort = msg.get("reasoning_effort")

            if not novel_id or not question:
                await websocket.send_json(
                    {"type": "error", "message": "Missing novel_id or question"}
                )
                continue
            if (
                (model is not None and not isinstance(model, str))
                or (
                    reasoning_effort is not None
                    and not isinstance(reasoning_effort, str)
                )
            ):
                await websocket.send_json(
                    {"type": "error", "message": "Invalid model profile"}
                )
                continue

            try:
                async for chunk in query_stream(
                    novel_id=novel_id,
                    question=question,
                    conversation_id=conversation_id,
                    model=model,
                    reasoning_effort=reasoning_effort,
                ):
                    await websocket.send_json(chunk)
            except WebSocketDisconnect:
                # Client disconnected mid-stream — propagate to outer handler.
                raise
            except Exception as e:
                logger.error(f"Chat stream error: {e}", exc_info=True)
                try:
                    await websocket.send_json(
                        {"type": "error", "message": str(e)}
                    )
                    await websocket.send_json({"type": "done"})
                except (WebSocketDisconnect, RuntimeError):
                    # Client already disconnected — error/done messages can't be sent.
                    pass

    except WebSocketDisconnect:
        logger.debug(f"Chat WS disconnected: session={session_id}")
