import threading
import json
import time
import os
from flask import Flask, jsonify, render_template_string
import websocket

app = Flask(__name__)

# In-memory storage (no Redis needed)
renko_bars = []
renko_lock = threading.Lock()
last_close = None
current_price = None

RENKO_SIZE = 0.50  # $0.50 per Renko box

def process_trade(price):
    global last_close, renko_bars, current_price
    current_price = price

    with renko_lock:
        if last_close is None:
            last_close = round(price, 2)
            return

        diff = price - last_close
        box = RENKO_SIZE

        if abs(diff) >= box:
            num_boxes = int(abs(diff) / box)
            direction = 1 if diff > 0 else -1

            for i in range(num_boxes):
                new_close = round(last_close + direction * box, 2)
                bar = {
                    'open': last_close,
                    'close': new_close,
                    'direction': 'up' if direction > 0 else 'down',
                    'time': time.time()
                }
                renko_bars.append(bar)
                last_close = new_close

                # Keep last 500 bars
                if len(renko_bars) > 500:
                    renko_bars.pop(0)

def on_message(ws, message):
    try:
        data = json.loads(message)
        price = float(data['p'])
        process_trade(price)
    except Exception as e:
        print(f"Message error: {e}")

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed, reconnecting in 5s...")

def on_open(ws):
    print("Connected to Binance WebSocket - LTCUSDT live feed active")

def feeder_thread():
    while True:
        try:
            url = "wss://stream.binance.com:9443/ws/ltcusdt@aggTrade"
            ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            ws.run_forever()
        except Exception as e:
            print(f"Feeder error: {e}")
        time.sleep(5)

@app.route('/')
def index():
    return render_template_string(HTML_CHART)

@app.route('/api/bars')
def get_bars():
    with renko_lock:
        return jsonify(renko_bars[-200:])

@app.route('/api/status')
def get_status():
    with renko_lock:
        return jsonify({
            'bars': len(renko_bars),
            'last_price': current_price,
            'last_renko': last_close
        })

HTML_CHART = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LTCUSDT Renko Chart</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', sans-serif; }
  #header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 20px; background: #161b22; border-bottom: 1px solid #30363d;
  }
  #header h1 { font-size: 18px; color: #58a6ff; }
  #status { font-size: 12px; color: #8b949e; }
  #price-display { font-size: 22px; font-weight: bold; }
  .up { color: #3fb950; }
  .down { color: #f85149; }
  #chart-container {
    width: 100%; overflow-x: auto; padding: 20px;
    height: calc(100vh - 120px);
  }
  canvas { display: block; }
  #info-bar {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #161b22; border-top: 1px solid #30363d;
    padding: 6px 20px; display: flex; gap: 20px;
    font-size: 12px; color: #8b949e;
  }
  #info-bar span { color: #e6edf3; }
</style>
</head>
<body>
<div id="header">
  <h1>📊 LTCUSDT Renko</h1>
  <div id="price-display">--</div>
  <div id="status">Connecting...</div>
</div>

<div id="chart-container">
  <canvas id="renkoCanvas"></canvas>
</div>

<div id="info-bar">
  Bars: <span id="bar-count">0</span>
  &nbsp;|&nbsp; Box Size: <span>$0.50</span>
  &nbsp;|&nbsp; Last Renko: <span id="last-renko">--</span>
  &nbsp;|&nbsp; <span id="last-dir">--</span>
</div>

<script>
const canvas = document.getElementById('renkoCanvas');
const ctx = canvas.getContext('2d');

let bars = [];

function drawChart() {
  if (bars.length === 0) return;

  const BAR_W = 14;
  const GAP = 2;
  const TOTAL_W = bars.length * (BAR_W + GAP) + 60;
  const H = window.innerHeight - 120;

  canvas.width = Math.max(TOTAL_W, window.innerWidth - 40);
  canvas.height = H;

  const prices = bars.flatMap(b => [b.open, b.close]);
  const minP = Math.min(...prices) - 1;
  const maxP = Math.max(...prices) + 1;
  const range = maxP - minP;

  function toY(p) { return H - ((p - minP) / range) * (H - 40) - 20; }

  ctx.fillStyle = '#0d1117';
  ctx.fillRect(0, 0, canvas.width, H);

  ctx.strokeStyle = '#21262d';
  ctx.lineWidth = 1;
  const steps = 8;
  for (let i = 0; i <= steps; i++) {
    const p = minP + (range / steps) * i;
    const y = toY(p);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
    ctx.fillStyle = '#484f58';
    ctx.font = '10px monospace';
    ctx.fillText(p.toFixed(2), 2, y - 2);
  }

  bars.forEach((bar, i) => {
    const x = 40 + i * (BAR_W + GAP);
    const y1 = toY(Math.max(bar.open, bar.close));
    const y2 = toY(Math.min(bar.open, bar.close));
    const barH = Math.max(y2 - y1, 2);

    ctx.fillStyle = bar.direction === 'up' ? '#3fb950' : '#f85149';
    ctx.fillRect(x, y1, BAR_W, barH);

    ctx.strokeStyle = bar.direction === 'up' ? '#2ea043' : '#da3633';
    ctx.lineWidth = 1;
    ctx.strokeRect(x, y1, BAR_W, barH);
  });

  const container = document.getElementById('chart-container');
  container.scrollLeft = canvas.width;
}

async function fetchBars() {
  try {
    const res = await fetch('/api/bars');
    bars = await res.json();

    const statusRes = await fetch('/api/status');
    const status = await statusRes.json();

    const priceEl = document.getElementById('price-display');
    if (status.last_price) {
      priceEl.textContent = '$' + status.last_price.toFixed(2);
    }

    document.getElementById('bar-count').textContent = status.bars;
    document.getElementById('last-renko').textContent =
      status.last_renko ? '$' + status.last_renko.toFixed(2) : '--';

    const statusEl = document.getElementById('status');
    if (bars.length > 0) {
      statusEl.textContent = '🟢 Live';
      statusEl.style.color = '#3fb950';
      const lastBar = bars[bars.length - 1];
      const dirEl = document.getElementById('last-dir');
      dirEl.textContent = lastBar.direction === 'up' ? '▲ Bullish' : '▼ Bearish';
      dirEl.style.color = lastBar.direction === 'up' ? '#3fb950' : '#f85149';
      priceEl.className = lastBar.direction === 'up' ? 'up' : 'down';
    } else {
      statusEl.textContent = '⏳ Waiting for data...';
    }

    drawChart();
  } catch(e) {
    document.getElementById('status').textContent = '🔴 Connection error';
  }
}

fetchBars();
setInterval(fetchBars, 2000);
window.addEventListener('resize', drawChart);
</script>
</body>
</html>
"""

if __name__ == '__main__':
    t = threading.Thread(target=feeder_thread, daemon=True)
    t.start()
    print("Feeder thread started")
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
