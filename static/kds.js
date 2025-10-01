// --- Simple WebSocket client ---
const wsUrl = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
const statusEl = document.getElementById('status');
const ordersEl = document.getElementById('orders');
let ws;

function connect() {
  ws = new WebSocket(wsUrl);
  ws.onopen = () => { statusEl.textContent = 'Connected'; };
  ws.onclose = () => { statusEl.textContent = 'Disconnected — retrying…'; setTimeout(connect, 1000); };
  ws.onerror = () => { statusEl.textContent = 'Error'; };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data || '{}');
    if (msg.type === 'order' && msg.data) {
      addOrderCard(msg.data);
      playBeep();
    } else if (msg.type === 'status' && msg.data) {
      updateOrderStatus(msg.data.order_id, msg.data.status);
    }
  };
}
connect();

// --- Audio: short notification beep using Web Audio API ---
function playBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = 'sine';
    o.frequency.setValueAtTime(880, ctx.currentTime); // A5
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
    o.connect(g).connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.26);
  } catch (e) {
    // Ignore audio errors (e.g., autoplay restrictions)
  }
}

// --- Render helpers ---
function addOrderCard(order) {
  const id = `order-${order.order_id}`;
  if (document.getElementById(id)) return; // avoid dupes

  const card = document.createElement('div');
  card.className = 'card new';
  card.id = id;

  const header = document.createElement('div');
  header.className = 'row';
  header.innerHTML = `
    <div>
      <strong>#${order.order_id}</strong>
      <span class="badge" title="source">${order.source || 'POS'}</span>
      <span class="badge" title="table">Table ${order.table || '-'}</span>
    </div>
    <div class="pill ${order.status || 'NEW'}">${order.status || 'NEW'}</div>
  `;

  const items = document.createElement('div');
  items.className = 'items';
  items.innerHTML = (order.items || []).map(it => {
    const mods = (it.mods && it.mods.length) ? ` <span class="muted">(${it.mods.join(', ')})</span>` : '';
    return `<div>• <strong>${it.qty || 1}×</strong> ${it.name}${mods}</div>`;
  }).join('');

  const footer = document.createElement('div');
  footer.className = 'row';
  const ts = new Date(order.created_at || Date.now()).toLocaleTimeString();
  footer.innerHTML = `
    <div class="muted">${ts}</div>
    <div class="btns">
      <button class="btn-start">Start</button>
      <button class="btn-done">Done</button>
      <button class="btn-bump">Bump</button>
    </div>
  `;

  // Wire buttons
  footer.querySelector('.btn-start').onclick = () => sendStatus(order.order_id, 'COOKING');
  footer.querySelector('.btn-done').onclick = () => sendStatus(order.order_id, 'DONE');
  footer.querySelector('.btn-bump').onclick = () => {
    card.remove();
  };

  card.appendChild(header);
  card.appendChild(items);
  card.appendChild(footer);
  ordersEl.prepend(card);

  // brief highlight
  setTimeout(() => card.classList.remove('new'), 800);
}

function updateOrderStatus(orderId, status) {
  const card = document.getElementById(`order-${orderId}`);
  if (!card) return;
  const pill = card.querySelector('.pill');
  if (pill) {
    pill.className = `pill ${status}`;
    pill.textContent = status;
  }
}

function sendStatus(orderId, status) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'status', order_id: orderId, status }));
  }
}
