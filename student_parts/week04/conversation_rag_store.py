from __future__ import annotations

"""SQLite 메시지를 메시지 1개당 ChromaDB document 1개로 동기화합니다."""

import hashlib
import json
from pathlib import Path
from typing import Any

from fixed.app_store import AppSQLiteStore
from fixed.config import CONFIG
from fixed.reference_store import OpenAIEmbeddingFunction


class ConversationMessageRAGStore:
    """SQLite messages row를 메시지 단위로 임베딩하는 저장소입니다."""

    COLLECTION_NAME = "kanana_conversation_messages_openai_v2"

    def __init__(
        self,
        chroma_dir: Path,
        *,
        embedding_function: Any | None = None,
        collection_name: str | None = None,
    ) -> None:
        import chromadb

        self.chroma_dir = chroma_dir
        self.collection_name = collection_name or self.COLLECTION_NAME
        self.embedding_function = (
            embedding_function
            or OpenAIEmbeddingFunction(
                api_key=CONFIG.proxy_token,
                base_url=CONFIG.embedding_proxy_url,
                model=CONFIG.openai_embedding_model,
            )
        )

        chroma_dir.mkdir(parents=True, exist_ok=True)

        client = chromadb.PersistentClient(path=str(chroma_dir))
        self.collection = client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_function,
            metadata={
                "description": "Kanana SQLite conversation messages",
                "chunk_unit": "message",
                "embedding_provider": "openai",
                "embedding_model": CONFIG.openai_embedding_model,
            },
        )

    def backend_info(self) -> dict[str, Any]:
        return {
            "vector_store": "chromadb",
            "chunk_unit": "message",
            "embedding_provider": "openai",
            "embedding_model": CONFIG.openai_embedding_model,
            "embedding_base_url": CONFIG.embedding_proxy_url,
            "collection_name": self.collection_name,
            "chroma_dir": str(self.chroma_dir),
        }

    def sync_from_sqlite(
        self,
        sqlite_store: AppSQLiteStore,
    ) -> dict[str, int]:
        """SQLite 메시지와 ChromaDB 메시지 청크를 동기화합니다."""

        chunks = self._message_chunks(sqlite_store)
        chunk_by_id = {
            chunk["chunk_id"]: chunk
            for chunk in chunks
        }

        existing = self._existing_metadata_by_id()

        stale_ids = [
            chunk_id
            for chunk_id in existing
            if chunk_id not in chunk_by_id
        ]

        if stale_ids:
            self.collection.delete(ids=stale_ids)

        upsert_chunks: list[dict[str, Any]] = []
        skipped = 0

        for chunk_id, chunk in chunk_by_id.items():
            existing_metadata = existing.get(chunk_id) or {}
            existing_hash = existing_metadata.get("source_hash")
            current_hash = chunk["metadata"]["source_hash"]

            if existing_hash == current_hash:
                skipped += 1
                continue

            upsert_chunks.append(chunk)

        if upsert_chunks:
            self.collection.upsert(
                ids=[
                    chunk["chunk_id"]
                    for chunk in upsert_chunks
                ],
                documents=[
                    chunk["content"]
                    for chunk in upsert_chunks
                ],
                metadatas=[
                    chunk["metadata"]
                    for chunk in upsert_chunks
                ],
            )

        return {
            "upserted": len(upsert_chunks),
            "skipped": skipped,
            "deleted": len(stale_ids),
            "total": len(chunks),
        }
    
    def search(
    self,
    *,
    query: str,
    top_k: int = 5,
    exclude_conversation_id: str | None = None,) -> list[dict[str, Any]]:
        """질의와 가까운 메시지를 ChromaDB에서 검색합니다."""

        query_text = str(query or "").strip()
        if not query_text:
            return []

        try:
            limit = int(top_k)
        except (TypeError, ValueError):
            limit = 5

        limit = max(1, min(limit, 50))

        collection_count = self.collection.count()
        if collection_count <= 0:
            return []

        query_kwargs: dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": min(limit, collection_count),
        }

        # 현재 대화 메시지는 검색 후보 단계에서부터 제외
        if exclude_conversation_id:
            query_kwargs["where"] = {
                "conversation_id": {
                    "$ne": exclude_conversation_id,
                }
            }

        result = self.collection.query(**query_kwargs)

        documents = (result.get("documents") or [[]])[0] or []
        metadatas = (result.get("metadatas") or [[]])[0] or []
        distances = (result.get("distances") or [[]])[0] or []
        ids = (result.get("ids") or [[]])[0] or []

        hits: list[dict[str, Any]] = []

        for index, document in enumerate(documents):
            metadata = (
                metadatas[index]
                if index < len(metadatas)
                else {}
            ) or {}

            chunk_id = (
                str(ids[index])
                if index < len(ids)
                else ""
            )

            message_id = str(
                metadata.get("message_id")
                or chunk_id.removeprefix("message:")
            )
            conversation_id = str(
                metadata.get("conversation_id") or ""
            )
            role = str(metadata.get("role") or "")

            distance = (
                distances[index]
                if index < len(distances)
                else None
            )

            hits.append(
                {
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "role": role,
                    "content": str(document or ""),
                    "distance": distance,
                }
            )

        return hits

    def _message_chunks(
        self,
        sqlite_store: AppSQLiteStore,
    ) -> list[dict[str, Any]]:
        """messages row 하나를 ChromaDB 청크 하나로 변환합니다."""

        with sqlite_store.connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        m.message_id,
                        m.conversation_id,
                        m.role,
                        m.content,
                        m.created_at
                    FROM messages m
                    JOIN conversations c
                      ON c.conversation_id = m.conversation_id
                    WHERE c.status IN ('active', 'archived')
                    ORDER BY
                        m.created_at ASC,
                        m.rowid ASC
                    """
                ).fetchall()
            ]

        chunks: list[dict[str, Any]] = []

        for row in rows:
            message_id = str(row.get("message_id") or "")
            conversation_id = str(row.get("conversation_id") or "")
            role = str(row.get("role") or "")
            content = str(row.get("content") or "")
            created_at = str(row.get("created_at") or "")

            if not message_id or not content.strip():
                continue

            source_hash = self._source_hash(
                message_id=message_id,
                conversation_id=conversation_id,
                role=role,
                content=content,
                created_at=created_at,
            )

            chunks.append(
                {
                    "chunk_id": f"message:{message_id}",
                    "content": content,
                    "metadata": {
                        "message_id": message_id,
                        "conversation_id": conversation_id,
                        "role": role,
                        "created_at": created_at,
                        "source_hash": source_hash,
                    },
                }
            )

        return chunks

    def _existing_metadata_by_id(
        self,
    ) -> dict[str, dict[str, Any]]:
        result = self.collection.get(
            include=["metadatas"],
        )

        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []

        return {
            str(chunk_id): metadatas[index] or {}
            for index, chunk_id in enumerate(ids)
        }
        
    def context_from_hits(
    self,
    hits: list[dict[str, Any]],) -> str:
        """메시지 검색 결과를 LLM이 읽기 쉬운 문자열로 만듭니다."""

        lines = ["[이전 대화 메시지 검색 결과]"]

        if not hits:
            lines.append("- 검색된 이전 메시지가 없습니다.")
            return "\n".join(lines)

        for index, hit in enumerate(hits, start=1):
            lines.append(
                f"[{index}] "
                f"message_id={hit.get('message_id', '')} | "
                f"conversation_id={hit.get('conversation_id', '')} | "
                f"role={hit.get('role', '')}"
            )
            lines.append(str(hit.get("content") or ""))

        return "\n\n".join(lines)

    @staticmethod
    def _source_hash(
        *,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        created_at: str,
    ) -> str:
        source = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "created_at": created_at,
        }

        encoded = json.dumps(
            source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()