from flask import Flask, send_from_directory, request, jsonify
from flask_sock import Sock
import json
from datetime import datetime
import re
from pathlib import Path
import os, sys
from dotenv import load_dotenv
load_dotenv()
stripe_secret_key = os.getenv('STRIPE_SECRET_KEY')

import stripe
stripe.api_key = stripe_secret_key



# Word numbers for naive qty detection
NUM_WORDS = {
    'one':1,'two':2,'three':3,'four':4,'five':5,
    'six':6,'seven':7,'eight':8,'nine':9,'ten':10
}

MENU = json.loads(Path('data/menu.json').read_text())

# Build indices from MENU
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

SEG_SPLIT = re.compile(r";|\band\b")  # split into segments

app = Flask(__name__, static_url_path="/static", static_folder="static")
sock = Sock(app)

# In-memory list of connected websocket clients
clients = set()

def validate_cart(cart: list, menu: dict):
    """Return (items_out, subtotal) by pricing the cart against the menu."""
    price_index = {it['id']: it['price']
                   for cat in menu.get('categories', [])
                   for it in cat.get('items', [])}
    # additions map by id
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
        # allow additions by id OR by name (fallback)
        add_total = 0.0
        for a in additions:
            if a in add_map:
                add_total += float(add_map[a]['price'])
            else:
                # match by name (case-insensitive) if ids weren’t used
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


@app.route("/")
def root():
    return send_from_directory("static", "index.html")

@app.get('/api/menu')
def api_menu():
    return jsonify(MENU)


'''
@app.post('/api/validate')
def api_validate():
    payload = request.get_json(silent=True) or {}
    # naive validation: ensure items exist, apply addition pricing
    items_out = []
    total = 0.0
    price_index = {it['id']: it['price'] for cat in MENU['categories'] for it in cat['items']}
    valid_adds = {ad['id']: ad for cat in MENU['categories'] for it in cat['items'] for ad in it.get('modifiers',{}).get('additions',[])}
    for it in payload.get('cart', []):
        base = price_index.get(it['id'])
        if base is None: return jsonify({'error': f"Unknown item id {it['id']}"}), 400
        qty = max(1, int(it.get('qty',1)))
        add_total = sum(valid_adds[a]['price'] for a in it.get('mods',{}).get('additions',[]) if a in valid_adds)
        line = (base + add_total) * qty
        total += line
        items_out.append({**it, 'unit_price': base, 'add_total': add_total, 'line_total': round(line,2)})
    return jsonify({'items': items_out, 'subtotal': round(total,2)})
'''
@app.post('/api/validate')
def api_validate():
    payload = request.get_json(silent=True) or {}
    try:
        items_out, subtotal = validate_cart(payload.get('cart', []), MENU)
        return jsonify({'items': items_out, 'subtotal': subtotal})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400



@app.post('/api/chat')
def api_chat():
    print("Got HERE: api_chat - 1")
    body = request.get_json(silent=True) or {}
    print("Got HERE: api_chat - 2")
    text = (body.get('text') or '').strip()
    print("Got HERE: api_chat - 3")
    if not text:
        return jsonify({'error':'empty text'}), 400
    print("Got HERE: api_chat - 4")

    lower = text.lower()
    items = []
    print("Got HERE: api_chat - 5")

    segments = [s.strip() for s in SEG_SPLIT.split(lower) if s.strip()]
    print("Got HERE: api_chat - 6")

    for seg in segments:
        print("Got HERE: api_chat - 7: "+ str(seg))
        # quantity
        qty = 1
        mnum = re.search(r"(\d+)", seg)
        print("Got HERE: api_chat - 8: "+ str(mnum))
        if mnum:
            print("Got HERE: api_chat - 9 ")
            qty = max(1, int(mnum.group(1)))
            print("Got HERE: api_chat - 10 "+str(qty))
        else:
            print("Got HERE: api_chat - 11 ")
            for w, n in NUM_WORDS.items():
                if re.search(rf"\b{w}\b", seg):
                    qty = n
                    break
        print("Got HERE: api_chat - 12 "+str(qty))

        # find item by name
        chosen = None
        print("ITEMS_INDEX="+str(ITEMS_INDEX.items()))
        for name, it in ITEMS_INDEX.items():
            print("Got HERE: api_chat - 13 name="+str(name)+" seg="+str(seg))
            if name in seg:
                chosen = it
                break
            for syn in it.get("synonyms", []):
                if syn in seg:
                    chosen = it
                    break
            if chosen:
                break

        print("Got HERE: api_chat - 14 chosen="+str(chosen))

        if not chosen:
            print("Got HERE: api_chat - 15 not chosen")
            for name, it in ITEMS_INDEX.items():
                if name.rstrip('s') in seg:
                    chosen = it
                    break
        if not chosen:
            continue  # skip unknown items

        # exclusions "no X"
        exclusions = []
        for ex in EXCLUSIONS_INDEX:
            if re.search(rf"no\s+{re.escape(ex)}", seg):
                exclusions.append(ex)

        # additions "add Y"
        additions = []
        for am in re.findall(r"add\s+([a-zA-Z_ ]+)", seg):
            token = am.strip()
            for cand in list(ADDITIONS_INDEX):
                if cand in token:
                    additions.append(cand)
        additions = list(dict.fromkeys(additions))  # dedupe


        print("Got HERE: api_chat - 16 chosen:    id="+str(chosen['id']))
        print("Got HERE: api_chat - 16 chosen:  name="+str(chosen['name']))
        print("Got HERE: api_chat - 16 chosen: price="+str(chosen['price']))
        print("Got HERE: api_chat - 16 chosen:   qty="+str(qty))
        items.append({
            'id': chosen['id'],
            'name': chosen['name'],
            'price': chosen['price'],
            'qty': qty,
            'mods': {'exclusions': exclusions, 'additions': additions}
        })
        print("Got HERE: api_chat - 17 items: "+str(items))

    # Reuse pricing validator
    '''v_resp = api_validate()
    data = v_resp.get_json() if hasattr(v_resp, 'get_json') else None
    if not data or 'error' in data:
        return jsonify({'error': data.get('error','validation failed')}), 400

    print("Got HERE: api_chat - 18 return: "+str({
        'message': "Here's what I understood. Ready to place it?",
        'items': data['items'],
        'subtotal': data['subtotal']
    }))
    return jsonify({
        'message': "Here's what I understood. Ready to place it?",
        'items': data['items'],
        'subtotal': data['subtotal']
    })
    '''

    try:
        items_out, subtotal = validate_cart(items, MENU)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    return jsonify({
        'message': "Here's what I understood. Ready to place it?",
        'items': items_out,
        'subtotal': subtotal
    })


@app.route("/api/order", methods=["POST"])
def api_order():
    """Accept an order JSON and broadcast to all connected KDS screens."""
    try:
        # Ensure JSON body and correct header
        if not request.data:
            return jsonify({"error": "Empty request body"}), 400

        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "Invalid or non-JSON body. Set Content-Type: application/json"}), 400

        # Basic validation
        required = ["order_id", "items"]
        missing = [k for k in required if k not in payload]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        # Enrich
        payload.setdefault("created_at", datetime.utcnow().isoformat() + "Z")
        payload.setdefault("status", "NEW")
        payload.setdefault("source", "Web")
        payload.setdefault("table", "-")

        # Broadcast
        dead = []
        for ws in list(clients):
            try:
                ws.send(json.dumps({"type": "order", "data": payload}))
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

        return jsonify({"ok": True, "order_id": payload.get("order_id")}), 200

    except Exception as e:
        # Never leak exceptions without a response; always return JSON
        return jsonify({"error": "Server error while processing order", "detail": str(e)}), 500

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
            metadata={'location_id': os.getenv('LOCATION_ID','demo')}
        )
        order = {
            'order_id': session.id[-6:],
            'source': body.get('source', 'Web'),
            'table': body.get('table', '-'),
            'items': body.get('cart', []),
            'created_at': datetime.utcnow().isoformat()+'Z',
            'status': 'NEW'
        }
        _deliver_to_kitchen(order)
        return jsonify({'url': session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

'''
@app.post('/api/checkout')
def api_checkout():
    print("Got HERE: checkout 1")
    body = request.get_json(silent=True) or {}
    print("Got HERE: checkout 2")
    v = api_validate().json if hasattr(api_validate(), 'json') else None # quick reuse in demo
    print("Got HERE: checkout 3")
    if not v or 'error' in v: return jsonify({'error': v.get('error','validation failed')}), 400
    print("Got HERE: checkout 4")

    subtotal = v['subtotal']
    print("Got HERE: checkout 5")
    processing_fee = round(max(0.5, subtotal * 0.03 + 0.30), 2) # pass Stripe fee to customer
    print("Got HERE: checkout 6")
    total = round(subtotal + processing_fee, 2)
    print("Got HERE: checkout 7")

    # Build line_items for Stripe Checkout (simple one-off product)
    line_items = []
    for it in v['items']:
        line_items.append({
            'price_data': {
            'currency': 'usd',
            'product_data': {'name': it['name']},
            'unit_amount': int(round((it['unit_price'] + it['add_total']), 2) * 100)
        },
        'quantity': it['qty']
    })
    print("Got HERE: checkout 8")
    line_items.append({
        'price_data': {
            'currency': 'usd',
            'product_data': {'name': 'Processing Fee'},
            'unit_amount': int(processing_fee * 100)
        },
        'quantity': 1
    })
    print("Got HERE: checkout 9")

    try:
        print("Got HERE: checkout 10")
        session = stripe.checkout.Session.create(
            mode='payment',
            line_items=line_items,
            success_url=os.getenv('CHECKOUT_SUCCESS_URL'),
            cancel_url=os.getenv('CHECKOUT_CANCEL_URL'),
            metadata={'location_id': os.getenv('LOCATION_ID','demo')}
        )
        # Optimistically push a NEW order to KDS so kitchen can start prep after webhook confirms
        print("Got HERE: checkout 11")
        order = {
            'order_id': session.id[-6:],
            'source': 'Web', 'table': body.get('table','-'),
            'items': body.get('cart', []), 'created_at': datetime.utcnow().isoformat()+'Z', 'status': 'NEW'
        }
        print("Got HERE: checkout 12")
        _deliver_to_kitchen(order)
        print("Got HERE: checkout 13")
        return jsonify({'url': session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
'''

@sock.route('/ws')
def ws(ws):
    # Register
    clients.add(ws)
    try:
        # Notify that screen joined (optional)
        ws.send(json.dumps({"type": "hello", "data": {"msg": "KDS connected"}}))
        while True:
            # The KDS may send status updates back (e.g., mark done)
            raw = ws.receive()
            if raw is None:
                break
            try:
                message = json.loads(raw)
            except Exception:
                continue

            # Example: {"type":"status", "order_id":"A123", "status":"DONE"}
            if message.get("type") == "status":
                # Broadcast status update to all clients
                dead2 = []
                for other in clients:
                    try:
                        other.send(json.dumps({"type":"status","data":message}))
                    except Exception:
                        dead2.append(other)
                for d in dead2:
                    clients.discard(d)
    finally:
        # Unregister
        clients.discard(ws)

@app.post('/webhook/stripe')
def stripe_webhook():
    payload = request.data
    sig = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    try:
        event = stripe.Webhook.construct_event(payload, sig, endpoint_secret)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        # Mark order as PAID and re-broadcast status
        order_id = session['id'][-6:]
        _broadcast_status(order_id, 'PAID')

    return jsonify({'ok': True})


def _deliver_to_kitchen(order: dict):
    if os.getenv('DELIVERY_MODE','KDS') == 'PRINTER':
        backend = os.getenv('PRINTER_BACKEND','PRINTNODE').upper()
        if backend == 'PRINTNODE':
            mod = import_module('printer_backends.printnode_backend')
            mod.PrintNodeBackend().send_order(order)
        # TODO: STAR -> printer_backends.star_cloudprnt, EPSON -> printer_backends.epson_epos

    # Always show on KDS too (nice during dev)
    for ws in list(clients):
        try: ws.send(json.dumps({'type':'order','data': order}))
        except: clients.discard(ws)

def _broadcast_status(order_id: str, status: str):
    for ws in list(clients):
        try: ws.send(json.dumps({'type':'status','data': {'order_id': order_id, 'status': status}}))
        except: clients.discard(ws)

if __name__ == "__main__":
    # Dev server (single process). For prod, use gunicorn/uvicorn w/ websockets support.
    app.run(host="0.0.0.0", port=5001, debug=True)
