# kds-demo
# Tech stack
React - javascript/html5/css
Python -  Flask

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
