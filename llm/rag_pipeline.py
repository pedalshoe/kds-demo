"""
KDS-AI RAG Pipeline
===================
Builds a ChromaDB vectorstore from menu data + FAQ documents.
Used by the mistral model to answer menu questions with retrieved context.

On first run:  embeddings are generated and persisted to ./chroma_db/
Subsequent:    loads from disk (fast startup)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from langchain_ollama import OllamaEmbeddings

logger = logging.getLogger(__name__)

CHROMA_DIR   = os.getenv("CHROMA_DIR", "./chroma_db")
EMBED_MODEL  = os.getenv("EMBED_MODEL", "nomic-embed-text")  # fast local embeddings
OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
COLLECTION_PREFIX = "kds_menu_rag_v2"


class MenuRAGPipeline:
    """
    Retrieval-Augmented Generation pipeline over menu + FAQ data.

    The vectorstore stores three document types:
      - menu_item   : individual menu items with full metadata
      - menu_faq    : common questions / allergen info
      - daily_spec  : specials / seasonal items (updated externally)
    """

    def __init__(self, menu_data: dict, faq_path: Optional[str] = None):
        self.menu_data = menu_data
        self.faq_path  = faq_path or str(Path(__file__).parent.parent / "data" / "faq.json")
        self.collection_name = self._build_collection_name(EMBED_MODEL)

        self.embeddings = OllamaEmbeddings(
            model=EMBED_MODEL,
            base_url=OLLAMA_URL,
        )

        self._client = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )

        self._collection = self._get_collection()

        self._ensure_indexed(menu_data)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(self, query: str, k: int = 3) -> list[dict]:
        """Return top-k most relevant menu chunks for the query."""
        try:
            query_embedding = self.embeddings.embed_query(query)
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(k, self._collection.count()),
                include=["documents", "metadatas", "distances"],
            )
            docs = []
            for text, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                similarity_score = 1 - dist
                score = max(0.0, min(1.0, similarity_score))
                docs.append({
                    "text":      text,
                    "metadata":  meta,
                    "score":     round(score, 4),
                })
            return docs
        except Exception as e:
            if self._is_dimension_mismatch(e):
                logger.warning("Resetting RAG collection after dimension mismatch during query: %s", e)
                self._reset_collection()
                self._ensure_indexed(self.menu_data)
                return self.retrieve(query, k)
            logger.error("RAG retrieve error: %s", e)
            return []

    def add_daily_special(self, name: str, description: str, price: float):
        """Dynamically add a daily special to the RAG store."""
        doc_id   = f"special_{hashlib.md5(name.encode()).hexdigest()[:8]}"
        doc_text = f"Daily special: {name} (${price:.2f}) — {description}"
        try:
            embedding = self.embeddings.embed_documents([doc_text])[0]
            self._collection.upsert(
                ids=[doc_id],
                documents=[doc_text],
                embeddings=[embedding],
                metadatas=[{"type": "daily_spec", "name": name, "price": price}],
            )
            logger.info("Added daily special to RAG: %s", name)
        except Exception as e:
            if self._is_dimension_mismatch(e):
                logger.warning("Resetting RAG collection after dimension mismatch during upsert: %s", e)
                self._reset_collection()
                self._ensure_indexed(self.menu_data)
                self.add_daily_special(name, description, price)
                return
            raise

    def stats(self) -> dict:
        return {
            "total_documents": self._collection.count(),
            "chroma_dir":      CHROMA_DIR,
            "embed_model":     EMBED_MODEL,
        }

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def _ensure_indexed(self, menu_data: dict):
        """Index menu items if not already present (idempotent by doc id)."""
        try:
            docs, ids, metas = self._build_menu_docs(menu_data)
            faq_docs, faq_ids, faq_metas = self._build_faq_docs()

            all_docs  = docs  + faq_docs
            all_ids   = ids   + faq_ids
            all_metas = metas + faq_metas

            existing = set(self._collection.get(ids=all_ids)["ids"])
            new_docs  = [(d, i, m) for d, i, m in zip(all_docs, all_ids, all_metas) if i not in existing]

            if not new_docs:
                logger.info("RAG vectorstore up-to-date (%d docs)", self._collection.count())
                return

            logger.info("Indexing %d new documents into ChromaDB...", len(new_docs))
            batch_docs, batch_ids, batch_metas = zip(*new_docs)
            embeddings = self.embeddings.embed_documents(list(batch_docs))

            self._collection.add(
                ids=list(batch_ids),
                documents=list(batch_docs),
                embeddings=embeddings,
                metadatas=list(batch_metas),
            )
            logger.info("RAG indexing complete. Total docs: %d", self._collection.count())
        except Exception as e:
            if self._is_dimension_mismatch(e):
                logger.warning("Resetting RAG collection after dimension mismatch during indexing: %s", e)
                self._reset_collection()
                self._ensure_indexed(menu_data)
                return
            raise

    def _get_collection(self):
        return self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine", "embed_model": EMBED_MODEL},
        )

    def _reset_collection(self):
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._collection = self._get_collection()

    @staticmethod
    def _build_collection_name(embed_model: str) -> str:
        safe_model = re.sub(r"[^a-z0-9]+", "_", embed_model.lower()).strip("_")
        return f"{COLLECTION_PREFIX}_{safe_model}"

    @staticmethod
    def _is_dimension_mismatch(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "dimension" in msg and "collection dimensionality" in msg

    @staticmethod
    def _build_menu_docs(menu: dict) -> tuple[list, list, list]:
        docs, ids, metas = [], [], []
        for cat in menu.get("categories", []):
            for item in cat.get("items", []):
                # rich text chunk for embedding
                excl = ", ".join(item.get("modifiers", {}).get("exclusions", []))
                adds = ", ".join(
                    f"{a['name']} (+${a['price']})"
                    for a in item.get("modifiers", {}).get("additions", [])
                )
                text = (
                    f"{item['name']} — category: {cat['name']}, "
                    f"price: ${item.get('price', 0):.2f}. "
                    f"{item.get('description', '')} "
                    f"Can exclude: {excl}. Available add-ons: {adds}."
                ).strip()

                doc_id = f"item_{item['id']}"
                docs.append(text)
                ids.append(doc_id)
                metas.append({
                    "type":     "menu_item",
                    "item_id":  item["id"],
                    "name":     item["name"],
                    "category": cat["name"],
                    "price":    item.get("price", 0),
                })
        return docs, ids, metas

    def _build_faq_docs(self) -> tuple[list, list, list]:
        faq_path = Path(self.faq_path)
        if not faq_path.exists():
            return [], [], []
        try:
            faqs = json.loads(faq_path.read_text())
            docs, ids, metas = [], [], []
            for i, faq in enumerate(faqs):
                text   = f"Q: {faq['question']}\nA: {faq['answer']}"
                doc_id = f"faq_{i}"
                docs.append(text)
                ids.append(doc_id)
                metas.append({"type": "menu_faq", "question": faq["question"]})
            return docs, ids, metas
        except Exception as e:
            logger.warning("Could not load FAQ data: %s", e)
            return [], [], []
