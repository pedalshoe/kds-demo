"""
KDS-AI LLM Orchestration Layer
================================
Routes requests between two local Ollama models:
  - llama3   : order intent extraction, conversational chat
  - mistral  : menu RAG queries, upsell suggestions, dietary advice

Pipeline:
  user_text → IntentClassifier → (llama3 | mistral+RAG) → structured response

Production note: swap OllamaLLM base_url to your remote Ollama instance URL.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from langchain_ollama import OllamaLLM
from langchain.prompts import PromptTemplate
from langchain.schema.runnable import RunnableSequence
from langchain.callbacks.base import BaseCallbackHandler

from .rag_pipeline import MenuRAGPipeline

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_ORDER = os.getenv("MODEL_ORDER", "llama3")      # order intent + chat
MODEL_RAG   = os.getenv("MODEL_RAG",   "mistral")    # RAG / upsell


class Intent(str, Enum):
    ORDER       = "order"       # user is placing / modifying an order
    QUESTION    = "question"    # asking about menu, ingredients, allergens
    UPSELL      = "upsell"      # follow-up that may benefit from suggestion
    CHITCHAT    = "chitchat"    # greeting, general chat
    UNKNOWN     = "unknown"


@dataclass
class LLMResponse:
    intent:     Intent
    model_used: str
    raw_text:   str
    structured: dict = field(default_factory=dict)
    latency_ms: int  = 0
    rag_docs:   list = field(default_factory=list)


class TimingCallback(BaseCallbackHandler):
    """Simple latency tracker attached to LangChain chains."""

    def __init__(self):
        self.start: float = 0.0
        self.elapsed_ms: int = 0

    def on_llm_start(self, *args, **kwargs):
        self.start = time.perf_counter()

    def on_llm_end(self, *args, **kwargs):
        self.elapsed_ms = int((time.perf_counter() - self.start) * 1000)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

INTENT_PROMPT = PromptTemplate.from_template(
    """You are a classifier for a restaurant ordering system.
Classify the following customer message into exactly one category:
  order      - placing, modifying, or confirming an order
  question   - asking about menu items, ingredients, allergens, or hours
  upsell     - asking for recommendations or what goes well with something
  chitchat   - greetings, thanks, or off-topic conversation
  unknown    - cannot determine

Respond with ONLY the category word, nothing else.

Message: {text}
Category:"""
)

ORDER_EXTRACTION_PROMPT = PromptTemplate.from_template(
    """You are a restaurant order-taking assistant. Extract the order from the message.
Respond ONLY with valid JSON in this exact format:
{{
  "items": [
    {{
      "name": "<item name matching the menu>",
      "qty": <integer>,
      "mods": {{
        "exclusions": ["<modifier>"],
        "additions":  ["<modifier>"]
      }}
    }}
  ],
  "message": "<brief friendly confirmation>"
}}

Available menu context:
{menu_context}

Customer message: {text}
JSON:"""
)

CHAT_PROMPT = PromptTemplate.from_template(
    """You are a friendly restaurant assistant for a taco kitchen.
Answer helpfully and briefly (2-3 sentences max).
Use the menu context below if relevant.

Menu context:
{menu_context}

Customer: {text}
Assistant:"""
)

RAG_UPSELL_PROMPT = PromptTemplate.from_template(
    """You are an enthusiastic restaurant assistant helping customers discover great food.
Based on the retrieved menu information below, give a helpful, specific recommendation.
Keep your response to 2-3 sentences. Sound natural, not sales-y.

Retrieved menu information:
{retrieved_docs}

Customer query: {text}
Response:"""
)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class KDSOrchestrator:
    """
    Main entry point for all LLM interactions in the KDS system.

    Usage:
        orch = KDSOrchestrator(menu_data)
        response = orch.process("I'd like two tacos please")
    """

    def __init__(self, menu_data: dict):
        self.menu_data = menu_data
        self._menu_context = self._build_menu_context(menu_data)

        # --- LLM clients ---
        self.llm_order = OllamaLLM(
            model=MODEL_ORDER,
            base_url=OLLAMA_BASE_URL,
            temperature=0.1,   # low temp for structured extraction
            num_predict=512,
        )
        self.llm_rag = OllamaLLM(
            model=MODEL_RAG,
            base_url=OLLAMA_BASE_URL,
            temperature=0.7,   # higher for natural language
            num_predict=256,
        )

        # --- RAG pipeline (ChromaDB + embeddings) ---
        self.rag = MenuRAGPipeline(menu_data)

        # --- LangChain chains ---
        self._intent_chain:    RunnableSequence = INTENT_PROMPT    | self.llm_order
        self._order_chain:     RunnableSequence = ORDER_EXTRACTION_PROMPT | self.llm_order
        self._chat_chain:      RunnableSequence = CHAT_PROMPT      | self.llm_order
        self._rag_chain:       RunnableSequence = RAG_UPSELL_PROMPT | self.llm_rag

        logger.info(
            "KDSOrchestrator ready | order_model=%s rag_model=%s ollama=%s",
            MODEL_ORDER, MODEL_RAG, OLLAMA_BASE_URL
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, text: str) -> LLMResponse:
        """Route text through the correct LLM pipeline and return a structured response."""
        timer = TimingCallback()

        # Step 1: classify intent
        intent_raw = self._invoke(self._intent_chain, {"text": text}, timer).strip().lower()
        intent = self._parse_intent(intent_raw)
        logger.debug("Intent classified: %s → %s", text[:40], intent)

        # Step 2: route to correct model/pipeline
        if intent == Intent.ORDER:
            return self._handle_order(text, timer)
        elif intent in (Intent.QUESTION, Intent.UPSELL):
            return self._handle_rag(text, intent, timer)
        else:
            return self._handle_chat(text, timer)

    def health_check(self) -> dict:
        """Ping both models; return status dict."""
        results = {}
        for name, llm in [(MODEL_ORDER, self.llm_order), (MODEL_RAG, self.llm_rag)]:
            try:
                t0 = time.perf_counter()
                llm.invoke("ping")
                results[name] = {
                    "status": "ok",
                    "latency_ms": int((time.perf_counter() - t0) * 1000)
                }
            except Exception as e:
                results[name] = {"status": "error", "detail": str(e)}
        return results

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    def _handle_order(self, text: str, timer: TimingCallback) -> LLMResponse:
        raw = self._invoke(
            self._order_chain,
            {"text": text, "menu_context": self._menu_context},
            timer,
        )
        structured = self._safe_parse_json(raw)
        return LLMResponse(
            intent=Intent.ORDER,
            model_used=MODEL_ORDER,
            raw_text=raw,
            structured=structured,
            latency_ms=timer.elapsed_ms,
        )

    def _handle_rag(self, text: str, intent: Intent, timer: TimingCallback) -> LLMResponse:
        # Retrieve relevant menu chunks from ChromaDB
        docs = self.rag.retrieve(text, k=3)
        doc_text = "\n\n".join(d["text"] for d in docs)

        raw = self._invoke(
            self._rag_chain,
            {"text": text, "retrieved_docs": doc_text},
            timer,
        )
        return LLMResponse(
            intent=intent,
            model_used=MODEL_RAG,
            raw_text=raw,
            structured={"message": raw.strip()},
            latency_ms=timer.elapsed_ms,
            rag_docs=docs,
        )

    def _handle_chat(self, text: str, timer: TimingCallback) -> LLMResponse:
        raw = self._invoke(
            self._chat_chain,
            {"text": text, "menu_context": self._menu_context},
            timer,
        )
        return LLMResponse(
            intent=Intent.CHITCHAT,
            model_used=MODEL_ORDER,
            raw_text=raw,
            structured={"message": raw.strip()},
            latency_ms=timer.elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _invoke(self, chain: RunnableSequence, inputs: dict, timer: TimingCallback) -> str:
        timer.start = time.perf_counter()
        result = chain.invoke(inputs)
        timer.elapsed_ms = int((time.perf_counter() - timer.start) * 1000)
        return result if isinstance(result, str) else str(result)

    @staticmethod
    def _parse_intent(raw: str) -> Intent:
        for intent in Intent:
            if intent.value in raw:
                return intent
        return Intent.UNKNOWN

    @staticmethod
    def _safe_parse_json(text: str) -> dict:
        """Extract JSON from model output even if wrapped in markdown fences."""
        import re
        # strip ```json ... ``` wrappers
        cleaned = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        # find first {...}
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {"message": text.strip(), "items": []}

    @staticmethod
    def _build_menu_context(menu: dict) -> str:
        lines = []
        for cat in menu.get("categories", []):
            lines.append(f"## {cat['name']}")
            for item in cat.get("items", []):
                price = item.get("price", 0)
                desc  = item.get("description", "")
                mods  = item.get("modifiers", {})
                excl  = ", ".join(mods.get("exclusions", []))
                adds  = ", ".join(a["name"] for a in mods.get("additions", []))
                line  = f"- {item['name']} (${price:.2f})"
                if desc:  line += f": {desc}"
                if excl:  line += f" | can exclude: {excl}"
                if adds:  line += f" | add-ons: {adds}"
                lines.append(line)
        return "\n".join(lines)