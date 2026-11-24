#!/usr/bin/env python3
"""Local bakery order manager. Start with: python3 app.py"""
import argparse
import csv
import io
import json
import re
import sqlite3
from datetime import date, timedelta
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'bakery.db'
START = '2026-11-23'

def connect():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con

def product(text):
    match = re.search(r'(\d+)\s+(dozen cupcakes|cakes?)', text, re.I)
    if not match:
        raise ValueError('Enter a whole number of cakes or dozens of cupcakes.')
    qty = int(match[1])
    if qty < 1 or qty > 1000:
        raise ValueError('Quantity must be between 1 and 1,000.')
    return ('cupcakes' if 'cupcake' in match[2].lower() else 'cake', qty)

def initialize():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,customer TEXT NOT NULL,product TEXT NOT NULL,qty INTEGER NOT NULL,pickup TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'accepted');
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,customer TEXT NOT NULL,kind TEXT NOT NULL,product TEXT,qty INTEGER,pickup TEXT NOT NULL,received TEXT NOT NULL,raw TEXT,status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS helpers(day TEXT PRIMARY KEY,booked TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY,day TEXT,detail TEXT);''')
        if c.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
            return
        sources = ROOT / 'data' if (ROOT / 'data' / 'existing_orders.csv').is_file() else ROOT.parent
        source = sources / 'existing_orders.csv'
        if not source.exists():
            raise RuntimeError('The sample data is missing. Keep the data folder beside app.py.')
        with source.open(newline='') as f:
            for row in csv.DictReader(f):
                p,q = product(row['item'])
                from datetime import datetime
                pickup = datetime.strptime(row['pickup_day'] + ' 2026','%a %d %b %Y').date().isoformat()
                c.execute('INSERT INTO orders(id,customer,product,qty,pickup) VALUES(?,?,?,?,?)',(int(row['order'].strip('#')),row['customer'],p,q,pickup))
        from datetime import datetime
        for i, day in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']):
            html = (sources / f'InstagramOrders_{day}.html').read_text()
            for text in re.findall(r'<p class="message">(.*?)</p>',html,re.S):
                text = unescape(text)
                name, order, pickup = re.search(r'Name: (.*?)\nOrder: (.*?)\nPickup Date: (.*)',text).groups()
                kind = 'cancel' if order.lower()=='cancel' else 'change' if order.lower().startswith('change') else 'new'
                p,q = (None,None) if kind=='cancel' else product(order)
                pickup = datetime.strptime(pickup+' 2026','%a %b %d %Y').date().isoformat()
                c.execute('INSERT INTO requests(customer,kind,product,qty,pickup,received,raw) VALUES(?,?,?,?,?,?,?)',(name,kind,p,q,pickup,(date.fromisoformat(START)+timedelta(days=i)).isoformat(),text))
        c.executemany('INSERT INTO settings VALUES(?,?)',[('initialized','1'),('today',START)])

def today(c):
    return c.execute("SELECT value FROM settings WHERE key='today'").fetchone()[0]

def hours(p,q):
    return q * (0.5 if p=='cupcakes' else 2)

def revenue(p,q):
    return q * (40 if p=='cupcakes' else 120)

def usage(c,day,exclude=None):
    return sum(hours(r['product'],r['qty']) for r in c.execute("SELECT * FROM orders WHERE pickup=? AND status='accepted'",(day,)) if r['id']!=exclude)

def capacity(c,day):
    return 12 if c.execute('SELECT 1 FROM helpers WHERE day=?',(day,)).fetchone() else 8

def assess(c,r):
    if r['received'] > today(c):
        return False,'This message has not arrived yet.',None
    if (date.fromisoformat(r['pickup'])-date.fromisoformat(today(c))).days < 2:
        return False,'Less than 2 days’ notice.',None
    target = None
    if r['kind']!='new':
        matches = c.execute("SELECT * FROM orders WHERE lower(customer)=lower(?) AND pickup=? AND status='accepted'",(r['customer'],r['pickup'])).fetchall()
        if len(matches)!=1:
            return False,'No unique accepted order to update.',None
        target = matches[0]['id']
    if r['kind']=='cancel':
        return True,'Eligible · frees baking capacity.',target
    projected = usage(c,r['pickup'],target) + hours(r['product'],r['qty'])
    cap = capacity(c,r['pickup'])
    if projected > cap:
        if cap==8 and projected<=12:
            return False,f'Book a helper first · {projected:g}h needed.',target
        return False,f'Over capacity · {projected:g}h needed, {cap}h available.',target
    return True,f'Fits the schedule · {projected:g} of {cap}h after acceptance.',target

def state():
    with connect() as c:
        now = today(c)
        reqs = []
        for r in c.execute('SELECT * FROM requests WHERE received<=? ORDER BY received,id',(now,)):
            r=dict(r); r['eligible'],r['reason'],_ = assess(c,r); reqs.append(r)
        orders=[dict(r) for r in c.execute('SELECT * FROM orders ORDER BY pickup,id')]
        days=[]
        calendar_days = {(date.fromisoformat(START)+timedelta(days=i)).isoformat() for i in range(9)}
        calendar_days.update(r['pickup'] for r in orders)
        calendar_days.update(r['pickup'] for r in c.execute('SELECT pickup FROM requests'))
        calendar_days.update(r['day'] for r in c.execute('SELECT day FROM helpers'))
        for d in sorted(calendar_days):
            active=[r for r in orders if r['pickup']==d and r['status']=='accepted']
            helper=capacity(c,d)==12
            gross=sum(revenue(r['product'],r['qty']) for r in active)
            days.append(dict(day=d,hours=usage(c,d),capacity=capacity(c,d),helper=helper,revenue=gross,net=gross-(100 if helper else 0),orders=len(active)))
        return dict(today=now,requests=reqs,orders=orders,days=days,activity=[dict(r) for r in c.execute('SELECT * FROM activity ORDER BY id DESC LIMIT 15')])

def mutate(data):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        now=today(c)
        action=data.get('action')
        if action=='advance':
            new=data.get('day','')
            if new<=now or new>'2026-11-29': raise ValueError('Choose a later day in the lecture week.')
            date.fromisoformat(new)
            c.execute("UPDATE settings SET value=? WHERE key='today'",(new,))
            detail=f'Advanced the practice date to {new}.'
        elif action=='helper':
            day=data.get('day','')
            if (date.fromisoformat(day)-date.fromisoformat(now)).days<2: raise ValueError('Helpers need at least 2 days’ notice.')
            if c.execute('SELECT 1 FROM helpers WHERE day=?',(day,)).fetchone(): raise ValueError('A helper is already booked for this day.')
            c.execute('INSERT INTO helpers VALUES(?,?)',(day,now))
            detail=f'Helper booked for {day}: +4 hours, $100.'
        elif action=='request':
            name=str(data.get('customer','')).strip()
            if not name or len(name)>80: raise ValueError('Enter a customer name (up to 80 characters).')
            p=data.get('product'); q=data.get('qty'); kind=data.get('kind')
            if kind not in ['new','change','cancel']: raise ValueError('Choose a request type.')
            if p not in ['cupcakes','cake']: raise ValueError('Choose cupcakes or cake.')
            if isinstance(q,bool) or not isinstance(q,int) or not 1<=q<=1000: raise ValueError('Enter a whole quantity from 1 to 1,000.')
            day=date.fromisoformat(data.get('pickup','')).isoformat()
            c.execute('INSERT INTO requests(customer,kind,product,qty,pickup,received,raw) VALUES(?,?,?,?,?,?,?)',(name,kind,p,q,day,now,'Manually entered request'))
            detail=f'New {kind} request from {name} for {day}.'
        elif action in ['accept','reject']:
            r=c.execute('SELECT * FROM requests WHERE id=?',(data.get('id'),)).fetchone()
            if not r or r['status']!='pending' or r['received']>now: raise ValueError('This request is no longer pending.')
            if action=='accept':
                ok,reason,target=assess(c,r)
                if not ok: raise ValueError(reason)
                if r['kind']=='new':
                    c.execute('INSERT INTO orders(customer,product,qty,pickup) VALUES(?,?,?,?)',(r['customer'],r['product'],r['qty'],r['pickup']))
                elif r['kind']=='cancel': c.execute("UPDATE orders SET status='cancelled' WHERE id=?",(target,))
                else: c.execute('UPDATE orders SET product=?,qty=? WHERE id=?',(r['product'],r['qty'],target))
            c.execute('UPDATE requests SET status=? WHERE id=?',('accepted' if action=='accept' else 'rejected',r['id']))
            detail=f"{action.title()}ed {r['kind']} request from {r['customer']} for {r['pickup']}."
        else: raise ValueError('Unknown action.')
        c.execute('INSERT INTO activity(day,detail) VALUES(?,?)',(now,detail))
    return state()

PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Batch & Butter · Bakery planner</title><style>
:root{--ink:#263b34;--green:#284f41;--muted:#6f7973;--line:#e2e6df;--bg:#f7f8f4;--orange:#a9502d}*{box-sizing:border-box}body{margin:0;font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--ink);background:var(--bg)}button,input,select{font:inherit}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.45}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #b37837;outline-offset:3px}header{height:88px;background:white;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 4%}.brand{display:flex;align-items:center;gap:12px}.logo{width:43px;height:43px;background:var(--green);color:#fff;border-radius:13px;display:grid;place-items:center;font:27px Georgia}.brand b{font:700 24px Georgia}.brand small{display:block;color:var(--muted);font-size:12px;letter-spacing:2px}.tag{font-size:14px;background:#f0f3ed;border:1px solid var(--line);padding:7px 12px;border-radius:30px}main{max-width:1450px;margin:auto;padding:36px 4%}h1{font:40px Georgia;margin:4px 0 8px;letter-spacing:-1px}h2{font:25px Georgia;margin:0}h3{font-size:16px;margin:0}.muted{color:var(--muted)}p{margin:5px 0}.eyebrow{color:var(--orange);font-size:12px;font-weight:700;letter-spacing:2px}.topline{display:flex;align-items:center;justify-content:space-between;gap:20px}.btn{border:1px solid var(--line);background:white;color:var(--ink);border-radius:8px;padding:9px 15px;font-size:14px;font-weight:600;white-space:nowrap}.primary{background:var(--green);color:white;border-color:var(--green)}.small{padding:6px 12px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:28px 0}.stat{background:white;border:1px solid var(--line);padding:20px 24px;border-radius:12px}.stat span{font-size:14px;color:var(--muted)}.stat b{display:block;font-size:32px;font-weight:500;margin:4px 0}.stat small{font-size:13px;color:var(--muted)}.layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:24px}.panel{background:#fff;border:1px solid var(--line);border-radius:13px;overflow:hidden;margin-bottom:24px}.panelhead{display:flex;justify-content:space-between;align-items:center;padding:22px 24px;gap:12px}.panelhead p{font-size:14px;color:var(--muted)}.bar{height:6px;background:#e9ede6;border-radius:10px;margin:8px 0}.bar i{display:block;height:100%;background:#74927c;border-radius:10px}.tabs{display:flex;gap:20px;border-bottom:1px solid var(--line);padding:0 24px;overflow:auto}.tab{border:0;background:none;padding:14px 0;color:var(--muted);white-space:nowrap;font-size:14px}.tab.active{border-bottom:2px solid var(--green);color:var(--green);font-weight:700}.request{display:grid;grid-template-columns:42px minmax(0,1fr) auto;gap:13px;padding:20px 24px;border-bottom:1px solid #edf0e9;align-items:start}.avatar{width:40px;height:40px;background:#f2e8df;color:#8e553b;display:grid;place-items:center;border-radius:50%;font-family:Georgia;font-size:20px}.request h3{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.request p{font-size:14px}.pill{font-size:12px;padding:3px 8px;border-radius:5px;background:#edf3eb;color:#365b40;font-weight:500}.pill.warn{background:#fcf0e7;color:#974a21}.pill.neutral{background:#f0f0ee;color:#626b64}.reason{margin-top:8px!important;color:#46634b}.reason.warn{color:#a14b25}.actions{display:flex;gap:7px;padding-top:5px}.side{padding:22px}.side h2{margin-bottom:15px}.row{display:flex;justify-content:space-between;margin:12px 0;font-size:14px;gap:12px}.separator{border-top:1px solid var(--line);margin:18px 0}.callout{background:#f1f5ee;padding:14px;border-radius:9px;font-size:14px;margin:16px 0}.side .btn{width:100%}.rules{padding:22px;background:#edf2e9;border:1px solid #dce5d6;border-radius:13px;font-size:14px}.rules p{margin:12px 0}.rules b{display:block}.empty{padding:35px;text-align:center;color:var(--muted)}.toolbar{display:flex;gap:10px;align-items:center;padding:16px 24px}.toolbar input{flex:1;min-width:0;border:1px solid var(--line);border-radius:7px;padding:9px 12px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:14px 24px;text-align:left;border-bottom:1px solid var(--line)}th{font-weight:500;color:var(--muted);background:#fafbf8}.scroll{overflow:auto}.activity{padding:12px 24px}.event{border-bottom:1px solid var(--line);padding:12px 0;font-size:14px}.event small{display:block;color:var(--muted)}footer{text-align:center;font-size:13px;color:var(--muted);padding:20px}dialog{border:1px solid var(--line);border-radius:15px;padding:28px;max-width:490px;width:92%;color:var(--ink)}dialog::backdrop{background:#18392e70}label{display:block;margin:14px 0;font-size:14px}label input,label select{display:block;width:100%;border:1px solid #bbc7ba;padding:10px;border-radius:7px;background:white;margin-top:6px}.dialogbuttons{display:flex;justify-content:flex-end;gap:10px;margin-top:22px}.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--ink);color:white;padding:13px 22px;border-radius:10px;z-index:10;max-width:90%;box-shadow:0 5px 20px #0002}.datecontrol{display:flex;gap:8px;align-items:center;font-size:14px}.datecontrol select{border:1px solid var(--line);border-radius:7px;padding:8px;background:#fff;color:var(--ink)}@media(max-width:1100px){.layout{grid-template-columns:minmax(0,1fr) 290px}.request{grid-template-columns:36px minmax(0,1fr)}.actions{grid-column:2}.stats{gap:10px}.stat{padding:16px}}@media(max-width:800px){header{height:auto;min-height:80px;padding:15px 5%;gap:12px}.tag{display:none}.topline{align-items:start;flex-wrap:wrap}h1{font-size:33px}.stats{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.layout aside{display:grid;grid-template-columns:1fr 1fr;gap:16px}.panelhead{padding:18px}.request{padding:18px}.datecontrol{flex-wrap:wrap}main{padding:25px 5%}}@media(max-width:500px){.layout aside{display:block}.brand b{font-size:21px}.panelhead{align-items:start}.stat b{font-size:27px}.actions{flex-wrap:wrap}.tabs{gap:16px;padding:0 18px}.datecontrol{font-size:12px}}

.calendar-panel{margin-top:28px;margin-bottom:0}.calendar-heading{padding:24px}.calendar-heading h2{font-size:29px;margin-top:6px}.calendar-controls{display:flex;gap:8px;flex-wrap:wrap}.calendar-scroll{overflow-x:auto}.calendar-week{display:grid;grid-template-columns:repeat(7,minmax(145px,1fr));min-width:1015px;padding:0 24px 24px;gap:0}.calendar-day{border:1px solid var(--line);border-right:0;background:#fff;min-height:270px;display:flex;flex-direction:column;padding:12px;gap:10px;position:relative;min-width:0}.calendar-day:first-child{border-radius:10px 0 0 10px}.calendar-day:last-child{border-right:1px solid var(--line);border-radius:0 10px 10px 0}.calendar-day.selected{background:#f5f8f2;box-shadow:inset 0 3px var(--green)}.calendar-day.weekend{background:#fafbf8}.calendar-day.selected.weekend{background:#f5f8f2}.calendar-day-head{display:flex;align-items:center;justify-content:space-between;gap:6px}.date-button{border:0;background:none;color:var(--ink);display:flex;align-items:center;gap:9px;padding:4px 0;text-align:left}.date-button .date-number{font:25px Georgia;min-width:32px;min-height:32px;display:grid;place-items:center}.date-button .weekday{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}.is-today .date-number{background:var(--green);color:#fff;border-radius:50%}.task-stack{display:flex;flex-direction:column;gap:8px;flex:1}.baking-task{border:1px solid #d7e4d7;border-left:3px solid #5a8163;border-radius:7px;background:#edf5eb;text-align:left;padding:10px 8px;width:100%;color:#294d32;white-space:normal;transition:box-shadow .15s,transform .15s}.baking-task:hover{box-shadow:0 3px 8px #294d3219;transform:translateY(-1px)}.baking-task.cake{border-color:#eddccc;border-left-color:#af7448;background:#fbf1e8;color:#754b2d}.baking-task b{font-size:14px;display:block;overflow-wrap:anywhere}.baking-task span{font-size:13px;display:block;margin-top:3px}.baking-task small{font-size:12px;display:block;opacity:.85;margin-top:5px}.no-tasks{font-size:13px;color:var(--muted);padding:12px 0}.day-capacity{font-size:12px;color:var(--muted);margin-top:10px}.helper-tag{font-size:12px;color:#4c6343;background:#edf2e6;border-radius:4px;padding:4px 6px}.calendar-footer{border-top:1px solid var(--line);padding:13px 24px;display:flex;gap:20px;flex-wrap:wrap;font-size:13px;color:var(--muted)}.legend{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}.cake-legend{background:#af7448}.cupcake-legend{background:#5a8163}.task-modal{max-width:540px}.task-modal h2{margin-top:8px;font-size:30px}.task-modal .row{font-size:16px}.task-description{font-size:16px;margin:10px 0 20px}.task-summary{background:#f4f7f0;padding:14px 18px;border-radius:10px;margin:16px 0}.task-modal .dialogbuttons{flex-wrap:wrap}@media(max-width:800px){.calendar-heading{flex-direction:column;align-items:stretch;gap:16px}.calendar-week{padding:0 18px 18px}.calendar-heading h2{font-size:25px}.calendar-controls .btn{font-size:13px;padding:8px 12px}.calendar-footer{gap:12px;padding:13px 18px}.calendar-day{min-height:240px}}
</style></head><body><header><div class="brand"><span class="logo">b.</span><div><b>Batch & Butter</b><small>THE HOME BAKERY PLANNER</small></div></div><span class="tag">A little more organized. A lot less juggling.</span></header><main><div class="topline"><div><div class="eyebrow">YOUR BAKERY, AT A GLANCE</div><h1>A good day starts with a plan.</h1><p class="muted">From the first DM to the final pickup. Everything in one place.</p></div><button class="btn primary" onclick="openRequest()">+ Add request</button></div><section class="panel calendar-panel" aria-label="Baking task calendar"><div class="panelhead calendar-heading"><div><div class="eyebrow">YOUR BAKING CALENDAR</div><h2 id="calendarRange"></h2><p>Confirmed tasks · Baked on pickup day · All pickups at 5 PM</p></div><div class="calendar-controls"><button class="btn" onclick="moveWeek(-1)" aria-label="Previous week">Previous</button><button class="btn" onclick="goToday()">Practice today</button><button class="btn" onclick="moveWeek(1)" aria-label="Next week">Next</button></div></div><div class="calendar-scroll" tabindex="0" aria-label="Weekly tasks, scroll horizontally on small screens"><div class="calendar-week" id="calendarWeek"></div></div><div class="calendar-footer"><span><span class="legend cake-legend"></span> Cakes</span><span><span class="legend cupcake-legend"></span> Cupcakes</span><span>Click a task to see its details.</span></div></section><div class="stats" id="stats"></div><div class="layout"><section><div class="panel"><div class="panelhead"><div><h2 id="listTitle">The request inbox</h2><p id="listSubtitle">Instagram messages, ready for your decision.</p></div><span class="pill neutral" id="count"></span></div><div class="tabs"><button class="tab active" data-tab="pending" onclick="setTab('pending')">Needs a decision</button><button class="tab" data-tab="history" onclick="setTab('history')">Decisions</button><button class="tab" data-tab="orders" onclick="setTab('orders')">Order book</button><button class="tab" data-tab="activity" onclick="setTab('activity')">Activity</button></div><div class="toolbar"><input id="search" placeholder="Search a customer…" aria-label="Search a customer" oninput="renderList()"><button class="btn small" onclick="location.href='/export.csv'">Export orders</button></div><div id="list"></div></div></section><aside><div class="panel side"><div class="eyebrow">DAILY WORKSPACE</div><h2 id="selectedDate"></h2><div id="dayDetail"></div></div><div class="panel side"><h3>Practice the week</h3><p class="muted" style="font-size:14px;margin:10px 0">Messages arrive as you move through the lecture week. Your decisions are saved.</p><div class="datecontrol"><select id="practiceDay" aria-label="Next practice day"></select><button class="btn small" onclick="advance()">Advance day</button></div><p id="clock" class="muted" style="font-size:13px;margin-top:12px"></p></div><div class="rules"><h3>The recipe for a smooth day</h3><p><b>Give it two days</b>Orders, edits, cancellations and helpers need 2 calendar days’ notice.</p><p><b>Keep the oven happy</b>8 hours a day. One helper adds 4 hours for $100.</p><p><b>Bake fresh, every time</b>All baking happens on pickup day.</p><div class="separator"></div><p>🧁 Cupcakes · $40 / dozen · 30 min<br>🍰 Cakes · $120 each · 2 hours</p></div></aside></div><footer>Local lecture app · Imported Instagram examples · No live Instagram connection</footer></main><dialog id="taskDialog" class="task-modal" aria-labelledby="taskTitle"><div class="eyebrow">CONFIRMED BAKING TASK</div><h2 id="taskTitle"></h2><div id="taskContent"></div><div class="dialogbuttons"><button class="btn" onclick="document.getElementById('taskDialog').close()">Close details</button><button class="btn primary" id="taskDayButton">View day’s orders</button></div></dialog><dialog id="requestDialog"><form id="requestForm"><h2>A new request</h2><p class="muted">Record the message, then decide in your inbox.</p><label>Customer name<input name="customer" required maxlength="80" autocomplete="off"></label><label>Request type<select name="kind"><option value="new">New order</option><option value="change">Change existing order</option><option value="cancel">Cancel existing order</option></select></label><label>Product<select name="product"><option value="cupcakes">Cupcakes (dozens)</option><option value="cake">Cake (each)</option></select></label><label>Quantity<input name="qty" type="number" min="1" max="1000" step="1" value="1" required></label><label>Pickup date<input name="pickup" type="date" required></label><p class="muted" style="font-size:13px">For edits or cancellations, use the existing customer name and pickup date.</p><div class="dialogbuttons"><button type="button" class="btn" onclick="requestDialog.close()">Close</button><button class="btn primary">Save request</button></div></form></dialog><dialog id="confirmDialog"><h2 id="confirmTitle"></h2><p id="confirmText" style="margin-top:14px"></p><div class="dialogbuttons"><button class="btn" onclick="confirmDialog.close()">Go back</button><button class="btn primary" id="confirmButton">Confirm</button></div></dialog><div id="toast" role="status" hidden></div><script>
let data,selected='2026-11-23',tab='pending',busy=false,calendarStart='2026-11-23';
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(s,options={weekday:'short',month:'short',day:'numeric'})=>new Date(s+'T12:00:00').toLocaleDateString('en-CA',options),money=n=>new Intl.NumberFormat('en-CA',{style:'currency',currency:'CAD',maximumFractionDigits:0}).format(n),label=r=>r.qty+' '+(r.product==='cupcakes'?'dozen cupcakes':r.qty===1?'cake':'cakes');
function notice(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>$('toast').hidden=true,5000)}
async function api(payload){if(busy)return false;busy=true;try{const res=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const out=await res.json();if(!res.ok)throw Error(out.error);data=out;render();notice('Saved. Your schedule is up to date.');return true}catch(e){notice(e.message);return false}finally{busy=false}}
function confirmAction(title,text,payload){$('confirmTitle').textContent=title;$('confirmText').textContent=text;$('confirmButton').onclick=async()=>{if(await api(payload))$('confirmDialog').close()};$('confirmDialog').showModal()}
function setTab(t){tab=t;renderList()}
function addDays(day,offset){let d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+offset);return d.toISOString().slice(0,10)}
function weekOf(day){const weekday=new Date(day+'T12:00:00Z').getUTCDay();return addDays(day,-((weekday+6)%7))}
function daySummary(day){return data.days.find(d=>d.day===day)||{day,hours:0,capacity:8,helper:false,revenue:0,net:0,orders:0}}
function selectDay(day){selected=day;render()}
function moveWeek(offset){calendarStart=addDays(calendarStart,offset*7);selected=calendarStart;render()}
function goToday(){calendarStart=weekOf(data.today);selected=data.today;render()}
function renderCalendar(){
 const end=addDays(calendarStart,6);
 $('calendarRange').textContent=fmt(calendarStart,{month:'long',day:'numeric'})+' – '+fmt(end,{month:'long',day:'numeric',year:'numeric'});
 $('calendarWeek').innerHTML=Array.from({length:7},(_,i)=>{
  const day=addDays(calendarStart,i),summary=daySummary(day),tasks=data.orders.filter(o=>o.pickup===day&&o.status==='accepted');
  return `<section class="calendar-day ${day===selected?'selected':''} ${i>=5?'weekend':''}" aria-label="${fmt(day,{weekday:'long',month:'long',day:'numeric'})}">
   <div class="calendar-day-head"><button class="date-button ${day===data.today?'is-today':''}" onclick="selectDay('${day}')" aria-pressed="${day===selected}" aria-label="Select ${fmt(day)}${day===data.today?', practice today':''}"><span class="weekday">${fmt(day,{weekday:'short'})}</span><span class="date-number">${+day.slice(-2)}</span></button></div>
   <div class="task-stack">${tasks.length?tasks.map(o=>`<button class="baking-task ${o.product==='cake'?'cake':'cupcakes'}" onclick="openTask(${o.id})" aria-label="View baking task for ${esc(o.customer)}, ${label(o)}, ${fmt(day)}"><b>${esc(o.customer)}</b><span>Bake ${label(o)}</span><small>${o.qty*(o.product==='cake'?2:.5)}h baking · Pickup 5 PM</small></button>`).join(''):'<p class="no-tasks">No baking tasks</p>'}</div>
   ${summary.helper?'<span class="helper-tag">Helper booked · +4h</span>':''}
   <div class="day-capacity">${summary.hours} / ${summary.capacity} oven hours<div class="bar"><i style="width:${summary.hours/summary.capacity*100}%"></i></div></div>
  </section>`;
 }).join('');
}
function openTask(id){
 const order=data.orders.find(o=>o.id===id&&o.status==='accepted');if(!order)return;
 selected=order.pickup;render();
 $('taskTitle').textContent=order.customer;
 const duration=order.qty*(order.product==='cake'?2:.5),price=order.qty*(order.product==='cake'?120:40),summary=daySummary(order.pickup);
 $('taskContent').innerHTML=`<p class="task-description">Bake ${label(order)} · Order #${order.id}</p><div class="task-summary"><div class="row"><span>Baking day</span><b>${fmt(order.pickup,{weekday:'long',month:'short',day:'numeric'})}</b></div><div class="row"><span>Customer pickup</span><b>5 PM</b></div></div><div class="row"><span>Product</span><b>${order.product==='cake'?'Cake':'Cupcakes'}</b></div><div class="row"><span>Quantity</span><b>${order.qty} ${order.product==='cake'?(order.qty===1?'cake':'cakes'):'dozen'}</b></div><div class="row"><span>Oven time needed</span><b>${duration} ${duration===1?'hour':'hours'}</b></div><div class="row"><span>Order value</span><b>${money(price)}</b></div><div class="row"><span>Status</span><span class="pill">Confirmed</span></div><div class="separator"></div><p class="muted">${summary.hours} of ${summary.capacity} baking hours booked for this day.${summary.helper?' A helper is booked.':''} Bake this order on the pickup day, ready for 5 PM.</p>`;
 $('taskDayButton').onclick=()=>{$('taskDialog').close();setTab('orders');$('listTitle').scrollIntoView({behavior:'smooth',block:'start'})};
 $('taskDialog').showModal();
}
function render(){let pending=data.requests.filter(r=>r.status==='pending'),active=data.orders.filter(r=>r.status==='accepted'),gross=data.days.reduce((s,d)=>s+d.revenue,0),helpers=data.days.filter(d=>d.helper).length;$('stats').innerHTML=[['Requests to review',pending.length,'Your decision makes it official'],['Confirmed orders',active.length,'Across the full schedule'],['Booked revenue',money(gross),'Before helper costs'],['After helper costs',money(gross-helpers*100),helpers+' helper day'+(helpers===1?'':'s')+' · excludes other expenses']].map(([a,b,c])=>`<div class="stat"><span>${a}</span><b>${b}</b><small>${c}</small></div>`).join('');renderCalendar();let d=daySummary(selected),lead=Math.round((new Date(selected)-new Date(data.today))/86400000);$('selectedDate').textContent=fmt(selected,{weekday:'long',month:'short',day:'numeric'});$('dayDetail').innerHTML=`<div class="row"><span>Oven time booked</span><b>${d.hours} / ${d.capacity} hours</b></div><div class="bar"><i style="width:${d.hours/d.capacity*100}%"></i></div><div class="row"><span>Still available</span><b>${d.capacity-d.hours} hours</b></div><div class="row"><span>Confirmed revenue</span><b>${money(d.revenue)}</b></div><div class="row"><span>Helper cost</span><b>${money(d.helper?100:0)}</b></div><div class="separator"></div><div class="row"><span>After helper costs</span><b>${money(d.net)}</b></div><div class="callout">${d.helper?'Your helper is booked. You have 12 hours of capacity.':lead<2?'The 2-day booking window has closed for this day.':'A little extra room? A helper adds 4 hours for $100.'}</div><button class="btn ${!d.helper&&lead>=2?'primary':''}" ${d.helper||lead<2?'disabled':''} onclick="bookHelper()">${d.helper?'Helper booked':lead<2?'Helper booking closed':'Book a helper · $100'}</button><button class="btn" style="margin-top:9px" onclick="setTab('orders')">View this day’s orders</button>`;$('clock').textContent='Current practice date: '+fmt(data.today);let options=Array.from({length:7},(_,i)=>({day:addDays('2026-11-23',i)})).filter(d=>d.day>data.today);$('practiceDay').innerHTML=options.map(d=>`<option value="${d.day}">${fmt(d.day)}</option>`).join('');$('practiceDay').disabled=!options.length;renderList()}
function renderList(){if(!data)return;document.querySelectorAll('[data-tab]').forEach(el=>el.classList.toggle('active',el.dataset.tab===tab));let search=$('search').value.toLowerCase();$('listTitle').textContent=tab==='orders'?'The order book':tab==='activity'?'A record of your decisions':'The request inbox';$('listSubtitle').textContent=tab==='orders'?'Orders for '+fmt(selected)+' · Pickup at 5 PM':tab==='activity'?'Every change, recorded as you go.':'Messages received through '+fmt(data.today)+' · All pickup dates';let rows;if(tab==='activity'){rows=data.activity.filter(r=>r.detail.toLowerCase().includes(search));$('list').innerHTML='<div class="activity">'+rows.map(r=>`<div class="event">${esc(r.detail)}<small>${fmt(r.day)}</small></div>`).join('')+'</div>'}else if(tab==='orders'){rows=data.orders.filter(r=>r.pickup===selected&&r.customer.toLowerCase().includes(search));$('list').innerHTML='<div class="scroll"><table><thead><tr><th>Customer</th><th>Order</th><th>Value</th><th>Status</th></tr></thead><tbody>'+rows.map(r=>`<tr><td><b>${esc(r.customer)}</b><br><span class="muted">#${r.id}</span></td><td>${label(r)}</td><td>${money(r.qty*(r.product==='cake'?120:40))}</td><td><span class="pill ${r.status==='cancelled'?'neutral':''}">${esc(r.status)}</span></td></tr>`).join('')+'</tbody></table></div>'}else{rows=data.requests.filter(r=>(tab==='pending'?r.status==='pending':r.status!=='pending')&&r.customer.toLowerCase().includes(search));$('list').innerHTML=rows.map(r=>`<article class="request"><div class="avatar">${esc(r.customer[0])}</div><div><h3>${esc(r.customer)} <span class="pill ${r.kind==='new'?'neutral':'warn'}">${r.kind==='new'?'New order':r.kind==='change'?'Change':'Cancellation'}</span></h3><p>${r.kind==='cancel'?'Cancel existing order':label(r)} · <b>${fmt(r.pickup)}</b></p><p class="muted">Received ${fmt(r.received)}${r.kind==='cancel'?'':' · '+money(r.qty*(r.product==='cake'?120:40))+' · '+(r.qty*(r.product==='cake'?2:.5))+' oven hours'}</p>${r.status==='pending'?`<p class="reason ${r.eligible?'':'warn'}">${r.eligible?'✓':'!'} ${esc(r.reason)}</p>`:`<p class="muted">Decision: ${esc(r.status)}</p>`}</div><div class="actions">${r.status==='pending'?`<button class="btn small" onclick="decide(${r.id},'reject')">Reject</button><button class="btn primary small" ${r.eligible?'':'disabled'} title="${esc(r.reason)}" onclick="decide(${r.id},'accept')">${r.kind==='cancel'?'Approve cancel':r.kind==='change'?'Apply change':'Accept'}</button>`:'<span class="pill neutral">Reviewed</span>'}</div></article>`).join('')}$('count').textContent=rows.length+' '+(tab==='orders'?'orders':'items');if(!rows.length)$('list').innerHTML='<div class="empty">'+(tab==='pending'?'All caught up. No requests need a decision.':tab==='orders'?'No orders for this day.':'Nothing here yet.')+'</div>'}
function decide(id,action){const r=data.requests.find(r=>r.id===id);confirmAction(action==='reject'?'Reject this request?':r.kind==='cancel'?'Cancel this order?':'Confirm this request?',`${r.customer} · ${r.kind==='cancel'?'Cancel existing order':label(r)} · ${fmt(r.pickup)}. ${action==='reject'?'The existing schedule stays as it is.':'This updates the confirmed schedule.'}`,{action,id})}
function bookHelper(){confirmAction('Book an extra pair of hands?',fmt(selected)+': add 4 hours of capacity for $100.',{action:'helper',day:selected})}
function advance(){let day=$('practiceDay').value;if(!day)return;confirmAction('Move to '+fmt(day)+'?', 'New messages will appear. The practice clock moves forward only. Pending requests will be checked against the new date.',{action:'advance',day})}
function openRequest(){$('requestForm').reset();$('requestForm').elements.pickup.value=selected;$('requestDialog').showModal()}
$('requestForm').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),payload=Object.fromEntries(f);payload.qty=Number(payload.qty);payload.action='request';if(await api(payload)){$('requestDialog').close();setTab('pending')}};
fetch('/api/state').then(r=>{if(!r.ok)throw Error('Could not load saved orders.');return r.json()}).then(s=>{data=s;selected=s.today;calendarStart=weekOf(s.today);render()}).catch(e=>notice(e.message));
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def send(self,status,body,content_type='application/json; charset=utf-8'):
        if not isinstance(body,bytes): body=body.encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path=='/': self.send(200,PAGE,'text/html; charset=utf-8')
        elif self.path=='/api/state': self.send(200,json.dumps(state()))
        elif self.path=='/export.csv':
            output=io.StringIO(); writer=csv.writer(output)
            writer.writerow(['Order','Customer','Product','Quantity','Pickup date','Pickup time','Status','Revenue CAD','Oven hours'])
            for r in state()['orders']:
                # Prevent spreadsheet formulas in user-entered customer names.
                name=r['customer']; name="'"+name if name.lstrip().startswith(('=','+','-','@')) else name
                writer.writerow([r['id'],name,r['product'],r['qty'],r['pickup'],'5 PM',r['status'],revenue(r['product'],r['qty']) if r['status']=='accepted' else 0,hours(r['product'],r['qty']) if r['status']=='accepted' else 0])
            self.send(200,output.getvalue(),'text/csv; charset=utf-8')
        else: self.send(404,json.dumps({'error':'Not found.'}))
    def do_POST(self):
        if self.path!='/api/action': self.send(404,'{}'); return
        # Only local same-origin requests may change records.
        origin=self.headers.get('Origin')
        if origin and origin!=f'http://{self.headers.get("Host")}':
            self.send(403,json.dumps({'error':'Use the app on this computer.'})); return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<1 or size>16384: raise ValueError('Invalid request size.')
            payload=json.loads(self.rfile.read(size))
            if not isinstance(payload,dict): raise ValueError('Invalid request.')
            self.send(200,json.dumps(mutate(payload)))
        except (ValueError,TypeError,KeyError) as e: self.send(400,json.dumps({'error':str(e)}))
        except sqlite3.Error: self.send(500,json.dumps({'error':'Could not save. Please try again.'}))

def main():
    global DB
    parser=argparse.ArgumentParser(description='Start the local bakery planner.')
    parser.add_argument('--db',type=Path,default=DB)
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args(); DB=args.db
    initialize()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Batch & Butter is ready: http://127.0.0.1:{args.port}\nKeep this window open. Press Control+C to stop.',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
