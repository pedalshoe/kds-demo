"""
KDS-AI Test Suite
=================
Run:  pytest tests/ -v --cov=llm --cov=app

Test categories
---------------
  Unit         — orchestrator routing logic, intent parsing, JSON extraction
  Integration  — Flask API contracts (no LLM required, uses mocks)
  LLM          — live Ollama calls (skipped if Ollama is unreachable)
  RAG          — ChromaDB retrieval quality scoring
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_MENU = {
    "categories": [
        {
            "name": "Tacos",
            "items": [
                {
                    "id": "taco_pastor",
                    "name": "Taco al Pastor",
                    "price": 3.50,
                    "description": "Marinated pork with pineapple",
                    "synonyms": ["pastor taco", "al pastor"],
                    "modifiers": {
                        "exclusions": ["cheese", "cilantro", "onion"],
                        "additions": [
                            {"id": "add_guac",  "name": "Guacamole",  "price": 1.50},
                            {"id": "add_jal",   "name": "Jalapeños",  "price": 0.50},
                        ]
                    }
                },
                {
                    "id": "taco_carnitas",
                    "name": "Carnitas Taco",
                    "price": 3.75,
                    "description": "Slow-cooked pulled pork",
                    "synonyms": ["carnitas"],
                    "modifiers": {
                        "exclusions": ["sour cream", "cheese"],
                        "additions": [
                            {"id": "add_guac",  "name": "Guacamole",  "price": 1.50},
                        ]
                    }
                }
            ]
        },
        {
            "name": "Drinks",
            "items": [
                {
                    "id": "drink_agua",
                    "name": "Agua Fresca",
                    "price": 2.50,
                    "description": "Fresh fruit water",
                    "modifiers": {"exclusions": [], "additions": []}
                }
            ]
        }
    ]
}


@pytest.fixture
def menu():
    return SAMPLE_MENU


@pytest.fixture
def app_client(menu, tmp_path, monkeypatch):
    """Flask test client with LLM mocked out."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fake")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))

    # Patch the menu read so we use sample data
    with patch("builtins.open", side_effect=_menu_open_mock(menu)):
        original_read_text = Path.read_text

        def menu_read_text(path_obj, *args, **kwargs):
            if str(path_obj).endswith("data/menu.json"):
                return json.dumps(menu)
            return original_read_text(path_obj, *args, **kwargs)

        with patch("pathlib.Path.read_text", autospec=True, side_effect=menu_read_text):
            import importlib
            import app as kds_app
            importlib.reload(kds_app)
            kds_app.app.config["TESTING"] = True
            with kds_app.app.test_client() as client:
                yield client


def _menu_open_mock(menu):
    """Return a side_effect that serves menu.json from memory."""
    import builtins
    original_open = builtins.open
    def side_effect(path, *args, **kwargs):
        if "menu.json" in str(path):
            from io import StringIO
            return StringIO(json.dumps(menu))
        return original_open(path, *args, **kwargs)
    return side_effect


# ── Unit: Intent parsing ──────────────────────────────────────────────────────

class TestIntentParsing:

    def test_order_intent_keywords(self):
        from llm.orchestrator import KDSOrchestrator, Intent
        with patch("llm.orchestrator.OllamaLLM"), patch("llm.orchestrator.MenuRAGPipeline"):
            orch = KDSOrchestrator.__new__(KDSOrchestrator)
        assert KDSOrchestrator._parse_intent("order")    == Intent.ORDER
        assert KDSOrchestrator._parse_intent("question") == Intent.QUESTION
        assert KDSOrchestrator._parse_intent("upsell")   == Intent.UPSELL
        assert KDSOrchestrator._parse_intent("chitchat") == Intent.CHITCHAT
        assert KDSOrchestrator._parse_intent("garbage")  == Intent.UNKNOWN

    def test_parse_intent_case_insensitive(self):
        from llm.orchestrator import KDSOrchestrator, Intent
        assert KDSOrchestrator._parse_intent("ORDER")    == Intent.ORDER
        assert KDSOrchestrator._parse_intent("  order ") == Intent.ORDER


# ── Unit: JSON extraction ─────────────────────────────────────────────────────

class TestJSONExtraction:

    def test_clean_json(self):
        from llm.orchestrator import KDSOrchestrator
        raw = '{"items": [{"name": "Taco al Pastor", "qty": 2}], "message": "Got it"}'
        result = KDSOrchestrator._safe_parse_json(raw)
        assert result["items"][0]["name"] == "Taco al Pastor"
        assert result["items"][0]["qty"]  == 2

    def test_json_wrapped_in_markdown(self):
        from llm.orchestrator import KDSOrchestrator
        raw = '```json\n{"items": [], "message": "Sure!"}\n```'
        result = KDSOrchestrator._safe_parse_json(raw)
        assert result["message"] == "Sure!"
        assert result["items"] == []

    def test_json_with_preamble(self):
        from llm.orchestrator import KDSOrchestrator
        raw = 'Sure! Here is the order:\n{"items": [{"name": "Carnitas Taco", "qty": 1}], "message": "1 Carnitas Taco"}'
        result = KDSOrchestrator._safe_parse_json(raw)
        assert result["items"][0]["name"] == "Carnitas Taco"

    def test_unparseable_returns_fallback(self):
        from llm.orchestrator import KDSOrchestrator
        raw = "Sorry, I could not understand that."
        result = KDSOrchestrator._safe_parse_json(raw)
        assert "message" in result
        assert result["items"] == []


# ── Unit: Menu context builder ────────────────────────────────────────────────

class TestMenuContextBuilder:

    def test_context_contains_item_names(self, menu):
        from llm.orchestrator import KDSOrchestrator
        ctx = KDSOrchestrator._build_menu_context(menu)
        assert "Taco al Pastor" in ctx
        assert "Carnitas Taco"  in ctx
        assert "Agua Fresca"    in ctx

    def test_context_contains_prices(self, menu):
        from llm.orchestrator import KDSOrchestrator
        ctx = KDSOrchestrator._build_menu_context(menu)
        assert "$3.50" in ctx
        assert "$3.75" in ctx

    def test_context_contains_modifiers(self, menu):
        from llm.orchestrator import KDSOrchestrator
        ctx = KDSOrchestrator._build_menu_context(menu)
        assert "Guacamole" in ctx
        assert "cilantro"  in ctx


# ── Integration: API contracts ────────────────────────────────────────────────

class TestAPIMenu:

    def test_menu_returns_200(self, app_client):
        resp = app_client.get("/api/menu")
        assert resp.status_code == 200

    def test_menu_has_categories(self, app_client):
        resp = app_client.get("/api/menu")
        data = resp.get_json()
        assert "categories" in data
        assert len(data["categories"]) > 0


class TestAPIValidate:

    def test_valid_cart(self, app_client):
        cart = [{"id": "taco_pastor", "qty": 2, "mods": {"additions": [], "exclusions": []}}]
        resp = app_client.post("/api/validate", json={"cart": cart})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subtotal"] == pytest.approx(7.00)

    def test_valid_cart_with_addition(self, app_client):
        cart = [{"id": "taco_pastor", "qty": 1, "mods": {"additions": ["add_guac"], "exclusions": []}}]
        resp = app_client.post("/api/validate", json={"cart": cart})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subtotal"] == pytest.approx(5.00)   # 3.50 + 1.50

    def test_unknown_item_returns_400(self, app_client):
        cart = [{"id": "does_not_exist", "qty": 1}]
        resp = app_client.post("/api/validate", json={"cart": cart})
        assert resp.status_code == 400

    def test_empty_cart(self, app_client):
        resp = app_client.post("/api/validate", json={"cart": []})
        assert resp.status_code == 200
        assert resp.get_json()["subtotal"] == 0.0


class TestAPIChat:
    """Test /api/chat with LLM mocked — validates regex fallback path."""

    def test_order_detected_by_regex(self, app_client):
        with patch("app.get_orchestrator", return_value=None):
            resp = app_client.post("/api/chat", json={"text": "two taco al pastor please"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subtotal"] == pytest.approx(7.00)
        assert len(data["items"]) == 1

    def test_empty_text_returns_400(self, app_client):
        resp = app_client.post("/api/chat", json={"text": ""})
        assert resp.status_code == 400

    def test_missing_text_returns_400(self, app_client):
        resp = app_client.post("/api/chat", json={})
        assert resp.status_code == 400

    def test_llm_order_response_shape(self, app_client):
        """When LLM returns a valid order structure, response includes model/intent."""
        mock_result = MagicMock()
        mock_result.intent.value = "order"
        mock_result.structured  = {
            "items":   [{"name": "Taco al Pastor", "qty": 1, "mods": {"exclusions": [], "additions": []}}],
            "message": "Got it! One Taco al Pastor."
        }
        mock_result.raw_text    = "..."
        mock_result.model_used  = "llama3.1"
        mock_result.latency_ms  = 250
        mock_result.rag_docs    = []

        mock_orch = MagicMock()
        mock_orch.process.return_value = mock_result

        with patch("app.get_orchestrator", return_value=mock_orch):
            resp = app_client.post("/api/chat", json={"text": "one taco al pastor"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["model"]  == "llama3.1"
        assert data["intent"] == "order"

    def test_llm_question_response_shape(self, app_client):
        """Non-order intents return message without items."""
        mock_result = MagicMock()
        mock_result.intent.value = "question"
        mock_result.structured   = {"message": "Yes, our tacos are gluten-free."}
        mock_result.raw_text     = "Yes, our tacos are gluten-free."
        mock_result.model_used   = "mistral"
        mock_result.latency_ms   = 400
        mock_result.rag_docs     = [{"text": "...", "score": 0.91}]

        mock_orch = MagicMock()
        mock_orch.process.return_value = mock_result

        with patch("app.get_orchestrator", return_value=mock_orch):
            resp = app_client.post("/api/chat", json={"text": "are your tacos gluten free?"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["intent"]   == "question"
        assert data["model"]    == "mistral"
        assert data["items"]    == []
        assert "rag_docs" in data


class TestAPIOrder:

    def test_valid_order_broadcast(self, app_client):
        order = {
            "order_id": "TEST01",
            "items":    [{"name": "Taco al Pastor", "qty": 1}],
            "source":   "Web",
            "table":    "3"
        }
        resp = app_client.post("/api/order", json=order)
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_missing_order_id_returns_400(self, app_client):
        resp = app_client.post("/api/order", json={"items": []})
        assert resp.status_code == 400

    def test_empty_body_returns_400(self, app_client):
        resp = app_client.post("/api/order", data=b"", content_type="application/json")
        assert resp.status_code == 400


class TestAPILLMHealth:

    def test_health_unavailable_when_no_orch(self, app_client):
        with patch("app.get_orchestrator", return_value=None):
            resp = app_client.get("/api/llm/health")
        assert resp.status_code == 503

    def test_health_ok_shape(self, app_client):
        mock_orch = MagicMock()
        mock_orch.health_check.return_value = {
            "llama3.1":  {"status": "ok",    "latency_ms": 120},
            "mistral": {"status": "ok",    "latency_ms": 145},
        }
        with patch("app.get_orchestrator", return_value=mock_orch):
            resp = app_client.get("/api/llm/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "llama3.1"  in data["models"]
        assert "mistral" in data["models"]


class TestN8NWebhook:

    def test_order_ready_event(self, app_client):
        payload = {"event": "order_ready", "data": {"order_id": "A123"}}
        resp = app_client.post("/webhook/n8n", json=payload)
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_unknown_event_still_200(self, app_client):
        payload = {"event": "unknown_event", "data": {}}
        resp = app_client.post("/webhook/n8n", json=payload)
        assert resp.status_code == 200


# ── RAG: retrieval quality ────────────────────────────────────────────────────

class TestRAGPipeline:

    @pytest.fixture
    def rag(self, menu, tmp_path):
        """RAG pipeline with real ChromaDB but mocked embeddings."""
        with patch("llm.rag_pipeline.OllamaEmbeddings") as MockEmbed:
            import hashlib
            def fake_embed_documents(texts):
                # deterministic fake embeddings based on text hash
                result = []
                for t in texts:
                    h = int(hashlib.md5(t.encode()).hexdigest(), 16)
                    vec = [(h >> (i * 4) & 0xF) / 15.0 for i in range(128)]
                    result.append(vec)
                return result
            def fake_embed_query(text):
                return fake_embed_documents([text])[0]

            instance = MockEmbed.return_value
            instance.embed_documents.side_effect = fake_embed_documents
            instance.embed_query.side_effect      = fake_embed_query

            from llm.rag_pipeline import MenuRAGPipeline
            pipeline = MenuRAGPipeline(
                menu_data=menu,
                faq_path=str(Path(__file__).parent.parent / "data" / "faq.json"),
            )
            # Override chroma dir to tmp
            pipeline._client = __import__("chromadb").PersistentClient(path=str(tmp_path / "chroma"))
            pipeline._collection = pipeline._client.get_or_create_collection("test_rag")
            pipeline._ensure_indexed(menu)
            yield pipeline

    def test_retrieve_returns_list(self, rag):
        results = rag.retrieve("vegetarian options", k=2)
        assert isinstance(results, list)

    def test_retrieve_respects_k(self, rag):
        results = rag.retrieve("tacos", k=1)
        assert len(results) <= 1

    def test_retrieve_has_required_keys(self, rag):
        results = rag.retrieve("gluten free", k=2)
        for doc in results:
            assert "text"     in doc
            assert "metadata" in doc
            assert "score"    in doc

    def test_scores_are_normalized(self, rag):
        results = rag.retrieve("pastor taco with guacamole", k=3)
        for doc in results:
            assert 0.0 <= doc["score"] <= 1.0

    def test_stats_structure(self, rag):
        stats = rag.stats()
        assert "total_documents" in stats
        assert stats["total_documents"] >= 0

    def test_add_daily_special(self, rag):
        before = rag.stats()["total_documents"]
        rag.add_daily_special("Birria Taco", "Braised beef with consommé", 5.50)
        after  = rag.stats()["total_documents"]
        assert after == before + 1

    def test_empty_query_returns_gracefully(self, rag):
        results = rag.retrieve("", k=3)
        assert isinstance(results, list)


# ── Live LLM tests (skipped if Ollama unreachable) ───────────────────────────

def _ollama_available():
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434", timeout=2)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_available(), reason="Ollama not running locally")
class TestLiveOllama:

    @pytest.fixture
    def orch(self, menu):
        from llm.orchestrator import KDSOrchestrator
        return KDSOrchestrator(menu)

    def test_health_check_both_models(self, orch):
        result = orch.health_check()
        assert "llama3.1"  in result
        assert "mistral" in result

    def test_order_intent_live(self, orch):
        result = orch.process("I'd like two tacos al pastor please")
        assert result.intent.value in ("order", "unknown")
        assert result.latency_ms > 0

    def test_question_routes_to_mistral(self, orch):
        result = orch.process("Do you have any vegetarian options?")
        assert result.intent.value in ("question", "upsell")
        assert result.model_used == os.getenv("MODEL_RAG", "mistral")

    def test_llm_response_time_under_10s(self, orch):
        result = orch.process("hello")
        assert result.latency_ms < 10_000, f"LLM took {result.latency_ms}ms — too slow"
