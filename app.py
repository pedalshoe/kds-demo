"""
KDS-AI  —  app.py  (enhanced with LLM orchestration + RAG)
============================================================
Additions over the original:
  POST /api/chat          — now routes through KDSOrchestrator (llama3 + mistral)
  POST /api/llm/chat      — raw LLM chat endpoint (demo / testing)
  GET  /api/llm/health    — health-check both Ollama models
  GET  /api/rag/stats     — vectorstore statistics
  POST /api/rag/special   — add a daily special to the RAG index
  POST /webhook/n8n       — receive callbacks from n8n automation workflows

All original routes (order, checkout, stripe webhook, WebSocket) are unchanged.
"""

from flask import Flask, send_from_directory, request, jsonify
from flask_sock import Sock
import json
from datetime import datetime
import re
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

stripe_secret_key = os.getenv('STRIPE_SECRET_KEY')
import stripe
stripe.api_key = stripe_secret_key

# ---------------------------------------------------------------------------
# LLM orchestration (lazy-loaded so app starts even if Ollama is offline)
# ---------------------------------------------------------------------------
_orchestrator = None

def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        try:
            from llm.orchestrator import KDSOrchestrator
            _orchestrator = KDSOrchestrator(MENU)
        except Exception as e:
            app.logger.warning("LLM orchestrator unavailable: %s", e)
    return _orchestrator

# ---------------------------------------------------------------------------
# Word numbers, menu loading (unchanged from original)
# ---------------------------------------------------------------------------
NUM_WORDS = {
    'one':1,'two':2,'three':3,'four':4,'five':5,
    'six':6,'seven':7,'eight':8,'nine':9,'ten':10
}
MENU = json.loads(Path('data/menu.json').read_text())

ITEMS_INDEX = {}
ADDITIONS_INDEX = set()
EXCLUSIONS_INDEX = set()
for cat in MENU.get('categories', []):
    for it in cat.get('items', []):
        ITEMS_INDEX[it['name'].lower()] = it
        for ex in it.get('modifiers',{}).get('exclusions',[]):
            EXCLUSIONS_INDEX.add(ex.lower())
        for ad in it.get('modifiers',{}).get('additions', []):
            ADDITIONS_INDEX.add(ad['id'].lower())
            ADDITIONS_INDEX.add(ad['name'].lower())

SEG_SPLIT = re.compile(r";|\band\b")

app = Flask(__name__, static_url_path="/static", static_folder="static")
sock = Sock(app)
clients = set()

# n8n outbound webhook (set in .env)
N8N_WEBHOOK_URL = os.getenv('N8N_WEBHOOK_URL', '')


# ===========================================================================
# Helpers (unchanged)
# ===========================================================================
def validate_cart(cart: list, menu: dict):
    price_index = {it['id']: it['price']
                   for cat in menu.get('categories', [])
                   for it in cat.get('items', [])}
    add_map = {ad['id']: ad
               for cat in menu.get('categories', [])
               for it in cat.get('items', [])
               for ad in it.get('modifiers', {}).get('additions', [])}
    items_out = []
    total = 0.0
    for it in cart or []:
        item_id = it.get('id')
        if item_id not in price_index:
            raise ValueError(f"Unknown item id {item_id}")
        qty = max(1, int(it.get('qty', 1)))
        additions = (it.get('mods') or {}).get('additions', [])
        add_total = 0.0
        for a in additions:
            if a in add_map:
                add_total += float(add_map[a]['price'])
            else:
                matches = [v for v in add_map.values()
                           if v['name'].lower() == str(a).lower()]
                if matches:
                    add_total += float(matches[0]['price'])
        base = float(price_index[item_id])
        line = (base + add_total) * qty
        total += line
        items_out.append({**it,
                          'unit_price': base,
                          'add_total': round(add_total, 2),
                          'line_total': round(line, 2)})
    return items_out, round(total, 2)


def _notify_n8n(event_type: str, payload: dict):
    """Fire-and-forget webhook to n8n for order automation."""
    if not N8N_WEBHOOK_URL:
        return
    try:
        import urllib.request, urllib.error
        body = json.dumps({"event": event_type, "data": payload}).encode()
        req  = urllib.request.Request(
            N8N_WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception as e:
        app.logger.warning("n8n notify failed: %s", e)


# ===========================================================================
# Static / menu
# ===========================================================================
@app.route("/")
def root():
    return send_from_directory("static", "index.html")

@app.get('/api/menu')
def api_menu():
    return jsonify(MENU)


# ===========================================================================
# Cart validation
# ===========================================================================
@app.post('/api/validate')
def api_validate():
    payload = request.get_json(silent=True) or {}
    try:
        items_out, subtotal = validate_cart(payload.get('cart', []), MENU)
        return jsonify({'items': items_out, 'subtotal': subtotal})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


# ===========================================================================
# ENHANCED: /api/chat  — LLM-powered order parsing
# ===========================================================================
@app.post('/api/chat')
def api_chat():
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'empty text'}), 400

    orch = get_orchestrator()

    # ---- LLM path (when Ollama is available) ----
    if orch:
        try:
            result = orch.process(text)
            if result.intent.value == 'order':
                # Validate LLM-extracted items against priced menu
                llm_items = result.structured.get('items', [])
                # Map by name if id missing
                resolved = []
                for it in llm_items:
                    name_key = it.get('name', '').lower()
                    menu_item = ITEMS_INDEX.get(name_key)
                    if not menu_item:
                        # fuzzy: startswith
                        for k, v in ITEMS_INDEX.items():
                            if k.startswith(name_key[:5]):
                                menu_item = v
                                break
                    if menu_item:
                        resolved.append({
                            'id':   menu_item['id'],
                            'name': menu_item['name'],
                            'price': menu_item['price'],
                            'qty':  it.get('qty', 1),
                            'mods': it.get('mods', {'exclusions': [], 'additions': []}),
                        })
                try:
                    items_out, subtotal = validate_cart(resolved, MENU)
                    return jsonify({
                        'message':    result.structured.get('message', "Here's what I found:"),
                        'items':      items_out,
                        'subtotal':   subtotal,
                        'intent':     result.intent.value,
                        'model':      result.model_used,
                        'latency_ms': result.latency_ms,
                    })
                except ValueError:
                    pass  # fall through to message-only response

            # Non-order intent — return conversational message
            return jsonify({
                'message':    result.raw_text.strip(),
                'items':      [],
                'subtotal':   0,
                'intent':     result.intent.value,
                'model':      result.model_used,
                'latency_ms': result.latency_ms,
                'rag_docs':   result.rag_docs,
            })

        except Exception as e:
            app.logger.error("LLM chat error: %s", e)
            # fall through to regex fallback

    # ---- Regex fallback (original logic) ----
    lower    = text.lower()
    items    = []
    segments = [s.strip() for s in SEG_SPLIT.split(lower) if s.strip()]

    for seg in segments:
        qty = 1
        mnum = re.search(r"(\d+)", seg)
        if mnum:
            qty = max(1, int(mnum.group(1)))
        else:
            for w, n in NUM_WORDS.items():
                if re.search(rf"\b{w}\b", seg):
                    qty = n
                    break

        chosen = None
        for name, it in ITEMS_INDEX.items():
            if name in seg:
                chosen = it
                break
            for syn in it.get("synonyms", []):
                if syn in seg:
                    chosen = it
                    break
            if chosen:
                break

        if not chosen:
            for name, it in ITEMS_INDEX.items():
                if name.rstrip('s') in seg:
                    chosen = it
                    break

        if not chosen:
            continue

        exclusions = [ex for ex in EXCLUSIONS_INDEX
                      if re.search(rf"no\s+{re.escape(ex)}", seg)]
        additions  = []
        for am in re.findall(r"add\s+([a-zA-Z_ ]+)", seg):
            token = am.strip()
            for cand in list(ADDITIONS_INDEX):
                if cand in token:
                    additions.append(cand)
        additions = list(dict.fromkeys(additions))

        items.append({
            'id':    chosen['id'],
            'name':  chosen['name'],
            'price': chosen['price'],
            'qty':   qty,
            'mods':  {'exclusions': exclusions, 'additions': additions}
        })

    try:
        items_out, subtotal = validate_cart(items, MENU)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    return jsonify({
        'message':  "Here's what I understood. Ready to place it?",
        'items':    items_out,
        'subtotal': subtotal,
        'intent':   'order',
        'model':    'regex_fallback',
    })


# ===========================================================================
# NEW: Raw LLM endpoint (for demos / testing individual model calls)
# ===========================================================================
@app.post('/api/llm/chat')
def api_llm_chat():
    """
    Demo endpoint — direct access to the LLM pipeline.
    Body: { "text": "...", "model": "llama3|mistral" }
    """
    body  = request.get_json(silent=True) or {}
    text  = (body.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'empty text'}), 400

    orch = get_orchestrator()
    if not orch:
        return jsonify({'error': 'LLM service unavailable'}), 503

    try:
        result = orch.process(text)
        return jsonify({
            'response':   result.raw_text.strip(),
            'intent':     result.intent.value,
            'model':      result.model_used,
            'latency_ms': result.latency_ms,
            'rag_docs':   result.rag_docs,
            'structured': result.structured,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.get('/api/llm/health')
def api_llm_health():
    """Ping both Ollama models and return latency."""
    orch = get_orchestrator()
    if not orch:
        return jsonify({'status': 'unavailable', 'models': {}}), 503
    return jsonify({'status': 'ok', 'models': orch.health_check()})


# ===========================================================================
# NEW: RAG endpoints
# ===========================================================================
@app.get('/api/rag/stats')
def api_rag_stats():
    orch = get_orchestrator()
    if not orch:
        return jsonify({'error': 'LLM service unavailable'}), 503
    return jsonify(orch.rag.stats())


@app.post('/api/rag/special')
def api_rag_special():
    """Add a daily special to the RAG vectorstore."""
    body = request.get_json(silent=True) or {}
    name  = body.get('name', '').strip()
    desc  = body.get('description', '').strip()
    price = float(body.get('price', 0))
    if not name:
        return jsonify({'error': 'name required'}), 400
    orch = get_orchestrator()
    if not orch:
        return jsonify({'error': 'LLM service unavailable'}), 503
    orch.rag.add_daily_special(name, desc, price)
    return jsonify({'ok': True, 'name': name})


# ===========================================================================
# NEW: n8n inbound webhook
# ===========================================================================
@app.post('/webhook/n8n')
def n8n_webhook():
    """
    Receive automation callbacks from n8n.
    Supports: order_ready, order_cancelled, inventory_alert
    """
    payload = request.get_json(silent=True) or {}
    event   = payload.get('event')
    data    = payload.get('data', {})

    if event == 'order_ready':
        _broadcast_status(data.get('order_id', ''), 'READY')
    elif event == 'order_cancelled':
        _broadcast_status(data.get('order_id', ''), 'CANCELLED')
    elif event == 'inventory_alert':
        # broadcast inventory alert to all KDS screens
        for ws in list(clients):
            try:
                ws.send(json.dumps({'type': 'inventory_alert', 'data': data}))
            except Exception:
                clients.discard(ws)

    return jsonify({'ok': True, 'event': event})


# ===========================================================================
# Order API (unchanged from original, + n8n notification)
# ===========================================================================
@app.route("/api/order", methods=["POST"])
def api_order():
    try:
        if not request.data:
            return jsonify({"error": "Empty request body"}), 400
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "Invalid or non-JSON body"}), 400

        required = ["order_id", "items"]
        missing  = [k for k in required if k not in payload]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        payload.setdefault("created_at", datetime.utcnow().isoformat() + "Z")
        payload.setdefault("status", "NEW")
        payload.setdefault("source", "Web")
        payload.setdefault("table", "-")

        dead = []
        for ws in list(clients):
            try:
                ws.send(json.dumps({"type": "order", "data": payload}))
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

        # Notify n8n of new order
        _notify_n8n("new_order", payload)

        return jsonify({"ok": True, "order_id": payload.get("order_id")}), 200
    except Exception as e:
        return jsonify({"error": "Server error", "detail": str(e)}), 500


# ===========================================================================
# Checkout (unchanged)
# ===========================================================================
@app.post('/api/checkout')
def api_checkout():
    body = request.get_json(silent=True) or {}
    try:
        items_out, subtotal = validate_cart(body.get('cart', []), MENU)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    processing_fee = round(max(0.5, subtotal * 0.03 + 0.30), 2)
    line_items = [
        {
            'price_data': {
                'currency': 'usd',
                'product_data': {'name': it['name']},
                'unit_amount': int(round(it['unit_price'] + it['add_total'], 2) * 100)
            },
            'quantity': it['qty']
        } for it in items_out
    ]
    line_items.append({
        'price_data': {
            'currency': 'usd',
            'product_data': {'name': 'Processing Fee'},
            'unit_amount': int(processing_fee * 100)
        },
        'quantity': 1
    })

    try:
        session = stripe.checkout.Session.create(
            mode='payment',
            line_items=line_items,
            success_url=os.getenv('CHECKOUT_SUCCESS_URL'),
            cancel_url=os.getenv('CHECKOUT_CANCEL_URL'),
            metadata={'location_id': os.getenv('LOCATION_ID', 'demo')}
        )
        order = {
            'order_id':   session.id[-6:],
            'source':     body.get('source', 'Web'),
            'table':      body.get('table', '-'),
            'items':      body.get('cart', []),
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'status':     'NEW'
        }
        _deliver_to_kitchen(order)
        _notify_n8n("checkout_started", {"session_id": session.id, "subtotal": subtotal})
        return jsonify({'url': session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===========================================================================
# WebSocket KDS screen
# ===========================================================================
@sock.route('/ws')
def ws(ws):
    clients.add(ws)
    try:
        ws.send(json.dumps({"type": "hello", "data": {"msg": "KDS connected"}}))
        while True:
            raw = ws.receive()
            if raw is None:
                break
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if message.get("type") == "status":
                dead2 = []
                for other in clients:
                    try:
                        other.send(json.dumps({"type": "status", "data": message}))
                    except Exception:
                        dead2.append(other)
                for d in dead2:
                    clients.discard(d)
    finally:
        clients.discard(ws)


# ===========================================================================
# Stripe webhook (unchanged)
# ===========================================================================
@app.post('/webhook/stripe')
def stripe_webhook():
    payload = request.data
    sig     = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    try:
        event = stripe.Webhook.construct_event(payload, sig, endpoint_secret)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    if event['type'] == 'checkout.session.completed':
        session  = event['data']['object']
        order_id = session['id'][-6:]
        _broadcast_status(order_id, 'PAID')
        _notify_n8n("payment_confirmed", {"order_id": order_id, "session": session['id']})

    return jsonify({'ok': True})


# ===========================================================================
# Internal helpers
# ===========================================================================
def _deliver_to_kitchen(order: dict):
    for ws in list(clients):
        try:
            ws.send(json.dumps({'type': 'order', 'data': order}))
        except Exception:
            clients.discard(ws)


def _broadcast_status(order_id: str, status: str):
    for ws in list(clients):
        try:
            ws.send(json.dumps({'type': 'status', 'data': {'order_id': order_id, 'status': status}}))
        except Exception:
            clients.discard(ws)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)