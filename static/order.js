let cart = []; // {id, name, price, qty, mods:{exclusions:[], additions:[]}}
const menuEl = document.getElementById('menu');
const totalEl = document.getElementById('total');
const countEl = document.getElementById('itemsCount');


fetch('/api/menu').then(r=>r.json()).then(renderMenu);


function renderMenu(menu){
(menu.categories||[]).forEach(cat=>{
const h = document.createElement('h2'); h.textContent = cat.name; menuEl.appendChild(h);
(cat.items||[]).forEach(it=>{
const card = document.createElement('div'); card.className='card';
card.innerHTML = `<div class="row"><strong>${it.name}</strong><span>— $${it.price.toFixed(2)}</span></div>
<div class="row">
<label>Qty <input type="number" min="1" value="1" style="width:60px"></label>
<button>Add</button>
</div>`;
const qtyInput = card.querySelector('input');
card.querySelector('button').onclick = ()=>{
const qty = parseInt(qtyInput.value||'1',10);
// simple modifiers demo
const exclusions = prompt('Exclusions (comma separated, e.g., cheese)')?.split(',').map(s=>s.trim()).filter(Boolean)||[];
const additions = prompt('Additions (comma separated, e.g., extra_salsa)')?.split(',').map(s=>s.trim()).filter(Boolean)||[];
cart.push({id:it.id,name:it.name,price:it.price,qty,mods:{exclusions,additions}});
updateTotals();
};
menuEl.appendChild(card);
});
});
}


function updateTotals(){
let total=0, count=0; cart.forEach(i=>{ total += i.price*i.qty; count += i.qty; });
totalEl.textContent = total.toFixed(2); countEl.textContent = count;
}


document.getElementById('payBtn').onclick = async ()=>{
// create a server-side Checkout Session with processing fee passed through
const body = { cart, table: 'web', source: 'Web' };
const res = await fetch('/api/checkout', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
const data = await res.json();
if(data.url){ location.href = data.url; }
else { alert('CML Checkout error: '+ (data.error||'unknown')); }
};
