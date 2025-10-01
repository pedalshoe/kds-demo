const log = document.getElementById('log');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');

function addMsg(text, who='bot', html=false){
  const div = document.createElement('div');
  div.className = `msg ${who==='me'?'me':'bot'}`;
  if (html) div.innerHTML = text; else div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function send(){
  const text = input.value.trim();
  if (!text) return;
  addMsg(text, 'me');
  input.value = '';

  try {
    console.log("got here: 1");
    const res = await fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ text }) });
    console.log("got here: 2");
    const data = await res.json();
    console.log("got here: 3="+data);
    if (data.error){ addMsg('Error: '+data.error); return; }

    // Render confirmation with items and total
    const lines = (data.items||[]).map(it => {
      const ex = (it.mods?.exclusions||[]).length ? ` <span class="pill">no ${it.mods.exclusions.join(', ')}</span>` : '';
      const ad = (it.mods?.additions||[]).length ? ` <span class="pill">add ${it.mods.additions.join(', ')}</span>` : '';
      return `• <strong>${it.qty}×</strong> ${it.name}${ex}${ad}`;
    }).join('<br>');

    const html = `
      <div>${data.message||'Got it! Here\'s your order:'}</div>
      <div style="margin-top:6px">${lines}</div>
      <div class="totals">Subtotal: $${(data.subtotal||0).toFixed(2)}</div>
      <div class="actions">
        <button id="confirm">Confirm & Pay</button>
        <button id="edit">Edit</button>
      </div>
    `;
    addMsg(html, 'bot', true);

    // Wire buttons
    const confirmBtn = log.querySelector('#confirm');
    const editBtn = log.querySelector('#edit');
    confirmBtn.onclick = async () => {
      try{
        const payload = { cart: data.items, table: 'chat', source: 'Chat' };
        const r = await fetch('/api/checkout', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
        const j = await r.json();
        if (j.url) window.location = j.url; else addMsg('Checkout error: '+(j.error||'unknown'));
      }catch(e){ addMsg('Checkout error: '+e.message); }
    };
    editBtn.onclick = () => addMsg('Sure — edit your message and resend.');

  } catch(e){ addMsg('Error: '+ e.message); }
}

sendBtn.onclick = send;
input.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });

// Seed greeting
addMsg('Hi! Tell me what you\'d like. I know your menu — say things like: “2 chicken tacos no sour cream, add guac; 1 churros”.');

