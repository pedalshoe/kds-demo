# kds-demo

# Tech Stack - llm integration
 - Two Ollama models routing correctly
 - RAG retrieving menu context
 - Intent classification
 - Structured order extraction
 - n8n automation firing
 - Slack notifications
 - Docker production stack
 - pytest suite
 - Stripe

# With Docker Run
docker compose up -d

use this convience script:

Command				What it does
./start-docker.sh		Start full stack
./start-docker.sh --build	Rebuild kds-app image then start
./start-docker.sh --clean	Wipe all volumes + rebuild from scratch (fixes chroma dimension issues)
./start-docker.sh --stop	Stop all services cleanly
./start-docker.sh --logs	Tail all container logs
./start-docker.sh --status	Show container states

# Check both models and latency
curl http://localhost/api/llm/health | python3 -m json.tool

# RAG vectorstore - confirm menu is indexed
curl http://localhost/api/rag/stats | python3 -m json.tool

# Test mistral + RAG (question intent)
curl -X POST http://localhost/api/llm/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "do you have any vegetarian options?"}' | python3 -m json.tool

# Test llama3 (order intent)
curl -X POST http://localhost/api/llm/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "I would like two tacos al pastor please"}' | python3 -m json.tool

# Test full order flow through chat + n8n notification
curl -X POST http://localhost/api/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "two tacos al pastor and one agua fresca"}' | python3 -m json.tool

# bring up n8n automation interface:
In a new browser window: Browse to: http://localhost:5678

# Run a test order and watch the n8n web:
curl -X POST http://localhost:5001/api/order -H 'Content-Type: application/json' -d '{ "order_id":"A123", "source":"Web", "table":"5","items":[ {"name":"Taco al Pastor","qty":2,"mods":["no cheese","extra salsa"]}, {"name":"Churros","qty":1} ] }'


# bring up KDS
Window 1: http://localhost:5001                    ← KDS kitchen display or: /static/kds.html
Window 2: http://localhost:5001/static/chat.html   ← chatbot order entry
Window 3: http://localhost:5001/static/order.html  ← web order form

# start Stripe:
stripe listen --forward-to localhost:5001/webhook/stripe

Test Stripe:
curl -X POST http://localhost:5001/api/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "cart": [
      {"id": "taco_pastor", "qty": 2, "mods": {"additions": [], "exclusions": []}}
    ],
    "source": "Web",
    "table": "3"
  }'

# Running the pplication:
Place an order in Window 2 or 3 and watch it appear instantly in Window 1 with a beep.

# Bring down the environment:
docker compose down



# Without Docker Run



# Tech stack - Orignal
React - javascript/html5/css
Python - Flask




React Startup and demo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
# Open the KDS screen in your browser:
# http://localhost:5000 - or start with 5001 on macos

In a different terminal, send a test order:

# on macos:
curl -X POST http://localhost:5001/api/order -H 'Content-Type: application/json' -d '{ "order_id":"A123", "source":"Web", "table":"5","items":[ {"name":"Taco al Pastor","qty":2,"mods":["no cheese","extra salsa"]}, {"name":"Churros","qty":1} ] }'

# Open a new / different browser and resend the a test order with a different order_id. You will see both browsers reflect the order and status

to get feedback from a transaction:
stripe listen --forward-to localhost:5001/webhook/stripe

# Screen shots of Interfaces and backend processing:

<p>A screenshot of the project interface across multiple screens<br/>
<img src="./media/kds_1.jpg" alt="A screenshot of the project interface across multiple screens" width="500" />
</p>


<p>Mobile view of screen<br/>
<img src="./media/kds_2.jpg" alt="Mobile view of screen" width="500" />
</p>


<p>A screenshot of the project interface<br/>
<img src="./media/kds_3.jpg" alt="A screenshot of the project interface" width="500" />
</p>


<p>Multiple orders<br/>
<img src="./media/kds_4.jpg" alt="Multiple orders" width="500" />
</p>


<p>Stripe backend<br/>
<img src="./media/kds_5.jpg" alt="Stripe backend" width="500" /><br/>
<img src="./media/kds_6.jpg" alt="Stripe backend" width="500" />
</p>


<p>The Chatbot interface for taking orders<br/>
<img src="./media/kds_7.png" alt="The Chatbot interface for taking orders" width="500" />
</p>


<p>Stripe listener<br/>
<img src="./media/kds_8.jpg" alt="Stripe listener" width="500" />
</p>


<p>Chatbot order screen<br/>
<img src="./media/kds_9.png" alt="Chatbot order screen" width="500" />
</p>

<p>Web interface for taking orders<br/>
<img src="./media/kds_10.png" alt="Web interface for taking orders" width="500" />
</p>


<p>Mobile device screen<br/>
<img src="./media/kds_11.png" alt="Mobile device screen" width="500" />
</p>
