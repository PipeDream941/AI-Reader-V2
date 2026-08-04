"""Book-specific adaptive ontology endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db import book_ontology_store, chapter_store, novel_store
from src.services.book_ontology_controller import BookOntologyController


router = APIRouter(
    prefix="/api/novels/{novel_id}/ontology",
    tags=["book-ontology"],
)


class ScanRequest(BaseModel):
    force: bool = False
    auto_activate: bool = True


async def _require_novel(novel_id: str) -> None:
    if not await novel_store.get_novel(novel_id):
        raise HTTPException(status_code=404, detail="小说不存在")


@router.get("")
async def get_ontology(novel_id: str):
    await _require_novel(novel_id)
    ontology = await book_ontology_store.load(novel_id)
    versions = await book_ontology_store.list_versions(novel_id)
    return {"ontology": ontology.model_dump(), "versions": versions}


@router.get("/versions")
async def get_ontology_versions(novel_id: str):
    await _require_novel(novel_id)
    return {"versions": await book_ontology_store.list_versions(novel_id)}


@router.get("/proposals")
async def get_proposals(novel_id: str, status: str | None = None):
    await _require_novel(novel_id)
    return {
        "proposals": await book_ontology_store.list_proposals(novel_id, status)
    }


@router.post("/scan/{chapter_num}")
async def scan_chapter(novel_id: str, chapter_num: int, body: ScanRequest):
    await _require_novel(novel_id)
    chapter = await chapter_store.get_chapter_content(novel_id, chapter_num)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    controller = BookOntologyController()
    try:
        return await controller.scan_chapter(
            novel_id,
            chapter_num,
            chapter["content"],
            force=body.force,
            auto_activate=body.auto_activate,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"本书结构分析失败：{exc}") from exc


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(novel_id: str, proposal_id: int):
    await _require_novel(novel_id)
    controller = BookOntologyController()
    try:
        ontology = await controller.activate_proposal(novel_id, proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="结构候选不存在") from exc
    return {"ontology": ontology.model_dump()}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(novel_id: str, proposal_id: int):
    await _require_novel(novel_id)
    controller = BookOntologyController()
    try:
        await controller.reject_proposal(novel_id, proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="结构候选不存在") from exc
    return {"status": "rejected"}


@router.get("/collections/{collection_id}/members")
async def get_collection_members(novel_id: str, collection_id: str):
    await _require_novel(novel_id)
    members = await book_ontology_store.list_collection_members(
        novel_id, collection_id
    )
    return {"members": [member.model_dump() for member in members]}
