import os
import threading
import time
import math
import sqlite3
import logging
import json
from datetime import datetime, timedelta
from collections import deque
import contextlib
from typing import Optional, Dict, List, Tuple, Any
import hmac
import hashlib

import pandas as pd
import numpy as np
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException, BinanceOrderException, BinanceOrderMinAmountException

# ==================== IMPORTS PARA AUTENTICAÇÃO ====================
import jwt

# ==================== CONFIGURAÇÃO DE LOGGING ====================
logging.basicConfig(
    level=logging.DEBUG,  # Alterado para DEBUG para ver todos os logs
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def log(message: str, level: str = 'info', emoji: str = '🤖'):
    emoji_map = {
        'info': 'ℹ️',
        'warning': '⚠️',
        'error': '❌',
        'debug': '🔍',
        'success': '✅',
        'trade': '💰',
        'risk': '🛡️',
        'reject': '🚫'
    }
    emoji = emoji_map.get(level, '🤖')
    
    if level == 'info':
        logger.info(f"{emoji} {message}")
    elif level == 'warning':
        logger.warning(f"{emoji} {message}")
    elif level == 'error':
        logger.error(f"{emoji} {message}")
    elif level == 'debug':
        logger.debug(f"{emoji} {message}")
    elif level == 'success':
        logger.info(f"{emoji} {message}")
    elif level == 'trade':
        logger.info(f"{emoji} {message}")
    elif level == 'risk':
        logger.warning(f"{emoji} {message}")
    elif level == 'reject':
        logger.warning(f"{emoji} {message}")

# ==================== CONFIGURAÇÃO ====================
def validate_config():
    required_vars = ["BINANCE_API_KEY", "BINANCE_SECRET_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        error_msg = f"Variáveis obrigatórias não configuradas: {missing}"
        log(error_msg, level='error')
        raise ValueError(error_msg)
    
    global LEVERAGE, RISK, RR, MAX_TRADES, DAILY_LOSS_LIMIT, INTERVAL, RISK_MAX, RISK_MIN, MIN_BACKTEST_CONFIDENCE, PROTECT_CAPITAL, MAX_PRICE
    
    LEVERAGE = int(os.getenv("LEVERAGE", "2"))
    RISK = float(os.getenv("RISK", "0.01"))
    RR = float(os.getenv("RR", "2"))
    MAX_TRADES = int(os.getenv("MAX_TRADES", "2"))  # Alterado para 2
    DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "0.02"))
    INTERVAL = int(os.getenv("INTERVAL", "10"))
    
    RISK_MAX = float(os.getenv("RISK_MAX", "0.015"))
    RISK_MIN = float(os.getenv("RISK_MIN", "0.005"))
    MIN_BACKTEST_CONFIDENCE = float(os.getenv("MIN_BACKTEST_CONFIDENCE", "40"))  # Restaurado para produção
    PROTECT_CAPITAL = os.getenv("PROTECT_CAPITAL", "true").lower() == "true"
    MAX_PRICE = float(os.getenv("MAX_PRICE", "20"))
    
    if not (0 < RISK <= 1):
        raise ValueError("RISK deve estar entre 0 e 1")
    
    if LEVERAGE < 1 or LEVERAGE > 125:
        raise ValueError("LEVERAGE deve estar entre 1 e 125")
    
    log("Configuração validada com sucesso", level='success')
    return True

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="Nostradamus Trading Bot",
    description="Bot de Trading para Binance Futures - Versão Híbrida 3.1.1",
    version="3.1.1"
)

# CORS CORRIGIDO - ACEITA TODAS AS ORIGENS E MÉTODOS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (dashboard)
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

security = HTTPBearer()

# ==================== RATE LIMITING ====================
class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self._lock = threading.Lock()  # FIX: thread-safe
    
    def wait_if_needed(self):
        with self._lock:
            now = time.time()
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            if len(self.calls) >= self.max_calls:
                sleep_time = self.calls[0] + self.period - now
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self.calls.append(now)

binance_rate_limiter = RateLimiter(max_calls=10, period=1)
public_rate_limiter = RateLimiter(max_calls=30, period=1)

# ==================== BINANCE CLIENT ====================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

validate_config()

client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY, testnet=BINANCE_TESTNET)

if BINANCE_TESTNET:
    client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
    log("🧪 MODO TESTNET ATIVADO — usando dinheiro fictício", level='warning')
else:
    log("💰 MODO PRODUÇÃO — usando conta real", level='info')

# ==================== DATABASE ====================
DB_DIR = os.getenv("DB_PATH", "/app/data")
if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

DB_FILE = os.path.join(DB_DIR, "bot_state.db")

@contextlib.contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            side TEXT,
            entry REAL,
            qty REAL,
            entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            stop_loss REAL,
            take_profit REAL,
            trailing_activated BOOLEAN DEFAULT 0,
            highest_price REAL,
            lowest_price REAL,
            risk_used REAL,
            backtest_confidence REAL
        )""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            exit_price REAL,
            quantity REAL,
            pnl REAL,
            pnl_percentage REAL,
            entry_time TIMESTAMP,
            exit_time TIMESTAMP,
            reason TEXT,
            risk_used REAL
        )""")
        
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY,
            trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            start_balance REAL,
            end_balance REAL
        )""")
        
        log("Banco de dados inicializado", level='success')

init_db()

# ==================== FUNÇÕES AUXILIARES ====================
def save_state(key: str, value: Any):
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, str(value))
        )

def load_state(key: str, default_value: Any) -> Any:
    with get_db_connection() as conn:
        result = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    if result:
        try:
            if isinstance(default_value, bool):
                return result[0].lower() == 'true'
            elif isinstance(default_value, (int, float)):
                return type(default_value)(result[0])
            return result[0]
        except:
            return default_value
    return default_value

def save_position(symbol: str, position_data: Dict):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO positions 
            (symbol, side, entry, qty, entry_time, stop_loss, take_profit, highest_price, lowest_price, risk_used, backtest_confidence) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            position_data['side'],
            position_data['entry'],
            position_data['qty'],
            position_data.get('entry_time', datetime.now().isoformat()),
            position_data.get('stop_loss'),
            position_data.get('take_profit'),
            position_data.get('highest_price', position_data['entry']),
            position_data.get('lowest_price', position_data['entry']),
            position_data.get('risk_used', RISK),
            position_data.get('backtest_confidence', 0)
        ))

def update_position(symbol: str, updates: Dict):
    with get_db_connection() as conn:
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values())
        values.append(symbol)
        conn.execute(f"UPDATE positions SET {set_clause} WHERE symbol = ?", values)

def delete_position(symbol: str):
    with get_db_connection() as conn:
        conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))

def load_positions() -> Dict:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM positions").fetchall()
    return {row['symbol']: dict(row) for row in rows}

def save_trade_to_history(trade_data: Dict):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO trade_history 
            (symbol, side, entry_price, exit_price, quantity, pnl, pnl_percentage, entry_time, exit_time, reason, risk_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_data['symbol'],
            trade_data['side'],
            trade_data['entry_price'],
            trade_data['exit_price'],
            trade_data['quantity'],
            trade_data['pnl'],
            trade_data['pnl_percentage'],
            trade_data['entry_time'],
            trade_data['exit_time'],
            trade_data.get('reason', 'manual'),
            trade_data.get('risk_used', RISK)
        ))

def update_daily_metrics(date: str, pnl: float, is_win: bool):
    with get_db_connection() as conn:
        existing = conn.execute("SELECT * FROM daily_metrics WHERE date = ?", (date,)).fetchone()
        current_balance = get_account_balance()
        if existing:
            conn.execute("""
                UPDATE daily_metrics 
                SET trades = trades + 1,
                    wins = wins + ?,
                    losses = losses + ?,
                    total_pnl = total_pnl + ?,
                    end_balance = ?
                WHERE date = ?
            """, (1 if is_win else 0, 0 if is_win else 1, pnl, current_balance, date))
        else:
            conn.execute("""
                INSERT INTO daily_metrics (date, trades, wins, losses, total_pnl, max_drawdown, start_balance, end_balance)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?)
            """, (date, 1 if is_win else 0, 0 if is_win else 1, pnl, abs(pnl) if pnl < 0 else 0, current_balance - pnl, current_balance))

# ==================== RATE LIMITED REQUESTS ====================
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((BinanceAPIException, BinanceRequestException, requests.exceptions.RequestException)),
    before_sleep=before_sleep_log(logger, logging.WARNING)
)
def safe_binance_request(func, *args, **kwargs):
    binance_rate_limiter.wait_if_needed()
    try:
        return func(*args, **kwargs)
    except BinanceAPIException as e:
        if e.code == -1003:
            log("Rate limit excedido. Aguardando...", level='warning')
            time.sleep(60)
            raise
        raise
    except (BinanceRequestException, requests.exceptions.RequestException) as e:
        log(f"Erro de conexão: {e}", level='warning')
        raise

# ==================== BINANCE PRECISION ====================
def load_symbol_filters() -> Dict:
    try:
        exchange_info = safe_binance_request(client.futures_exchange_info)
        filters = {}
        for s in exchange_info["symbols"]:
            if not s["symbol"].endswith("USDT"):
                continue
            symbol_filters = {f["filterType"]: f for f in s["filters"]}
            if "LOT_SIZE" not in symbol_filters or "PRICE_FILTER" not in symbol_filters:
                continue
            lot_size = symbol_filters["LOT_SIZE"]
            price_filter = symbol_filters["PRICE_FILTER"]
            min_notional = symbol_filters.get("MIN_NOTIONAL", {})
            filters[s["symbol"]] = {
                "step": float(lot_size["stepSize"]),
                "min_qty": float(lot_size["minQty"]),
                "max_qty": float(lot_size.get("maxQty", float('inf'))),
                "tick": float(price_filter["tickSize"]),
                "min_price": float(price_filter.get("minPrice", 0)),
                "max_price": float(price_filter.get("maxPrice", float('inf'))),
                "min_notional": float(min_notional.get("notional", 5)) if min_notional else 5
            }
        return filters
    except Exception as e:
        log(f"Erro ao carregar filtros: {e}", level='error')
        return {}

symbol_filters = load_symbol_filters()

def adjust_qty(symbol: str, qty: float) -> float:
    if symbol not in symbol_filters:
        return qty
    filters = symbol_filters[symbol]
    step = filters["step"]
    min_qty = filters["min_qty"]
    max_qty = filters["max_qty"]
    precision = int(round(-math.log10(step)))
    qty = math.floor(qty / step) * step
    qty = round(qty, precision)
    qty = max(qty, min_qty)
    qty = min(qty, max_qty)
    return qty

def adjust_price(symbol: str, price: float) -> float:
    if symbol not in symbol_filters:
        return price
    filters = symbol_filters[symbol]
    tick = filters["tick"]
    min_price = filters["min_price"]
    max_price = filters["max_price"]
    precision = int(round(-math.log10(tick)))
    price = round(price / tick) * tick
    price = round(price, precision)
    price = max(price, min_price)
    price = min(price, max_price)
    return price

def validate_order(symbol: str, qty: float, price: Optional[float] = None) -> Tuple[bool, str]:
    if symbol not in symbol_filters:
        return False, "Símbolo não encontrado"
    filters = symbol_filters[symbol]
    if qty < filters["min_qty"]:
        return False, f"Quantidade abaixo do mínimo: {qty} < {filters['min_qty']}"
    if qty > filters["max_qty"]:
        return False, f"Quantidade acima do máximo: {qty} > {filters['max_qty']}"
    if price:
        min_notional = filters["min_notional"]
        notional_value = qty * price
        if notional_value < min_notional:
            return False, f"Valor nocional abaixo do mínimo: {notional_value} < {min_notional}"
    return True, "OK"

# ==================== FUNÇÕES UTILITÁRIAS ====================
def get_current_price(symbol: str) -> Optional[float]:
    try:
        return float(safe_binance_request(client.futures_symbol_ticker, symbol=symbol)["price"])
    except:
        return None

def get_account_balance() -> float:
    try:
        for b in safe_binance_request(client.futures_account_balance):
            if b["asset"] == "USDT":
                return float(b["balance"])
        return 0.0
    except:
        return 0.0

def get_candles(symbol: str, tf: str, limit: int = 200) -> pd.DataFrame:
    try:
        data = safe_binance_request(client.futures_klines, symbol=symbol, interval=tf, limit=limit)
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame()
        df.columns = ["t", "o", "h", "l", "c", "v", "ct", "qv", "n", "tb", "tq", "i"]
        for col in ["o", "h", "l", "c", "v", "qv", "tb", "tq"]:
            df[col] = df[col].astype(float)
        df["t"] = pd.to_datetime(df["t"], unit='ms')
        return df
    except:
        return pd.DataFrame()

# ==================== INDICADORES ====================
def ema(df: pd.DataFrame, span: int = 200) -> pd.Series:
    return df["c"].ewm(span=span).mean() if not df.empty else pd.Series()

def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if df.empty or len(df) < period + 1:
        return pd.Series()
    delta = df["c"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if df.empty or len(df) < period + 1:
        return pd.Series()
    tr = pd.concat([
        df["h"] - df["l"],
        abs(df["h"] - df["c"].shift()),
        abs(df["l"] - df["c"].shift())
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2) -> Dict:
    if df.empty or len(df) < period:
        return {}
    tp = (df["h"] + df["l"] + df["c"]) / 3
    ma = tp.rolling(period).mean()
    std = tp.rolling(period).std()
    return {"upper": ma + std * std_dev, "middle": ma, "lower": ma - std * std_dev}

def macd(df: pd.DataFrame) -> Dict:
    if df.empty or len(df) < 26:
        return {}
    exp1 = df["c"].ewm(span=12).mean()
    exp2 = df["c"].ewm(span=26).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=9).mean()
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}

def trend(df: pd.DataFrame) -> str:
    if df.empty or len(df) < 200:
        return "SIDE"
    ema200 = ema(df)
    if ema200.empty:
        return "SIDE"
    return "UP" if df["c"].iloc[-1] > ema200.iloc[-1] else "DOWN" if df["c"].iloc[-1] < ema200.iloc[-1] else "SIDE"

def calculate_score(symbol: str) -> Tuple[int, str]:
    df5 = get_candles(symbol, "5m")
    df15 = get_candles(symbol, "15m")
    df1h = get_candles(symbol, "1h")
    if df5.empty or df15.empty or df1h.empty:
        return 0, "SIDE"

    t5 = trend(df5)
    t15 = trend(df15)
    t1h = trend(df1h)
    r = rsi(df5).iloc[-1] if not rsi(df5).empty else 50
    a = atr(df5).iloc[-1] if not atr(df5).empty else 0.001

    score = 0
    if t5 == t15 == t1h:
        score += 50
    elif t5 == t15 or t5 == t1h or t15 == t1h:
        score += 25
    if df5["c"].iloc[-1] != 0 and (a / df5["c"].iloc[-1]) > 0.002:
        score += 20
    if (t5 == "UP" and 55 <= r <= 75) or (t5 == "DOWN" and 25 <= r <= 45):
        score += 20
    return score, t5

# ==================== FUNÇÕES DO CÓDIGO TS CONVERTIDAS ====================

def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> List[Dict]:
    """Encontra pontos de swing (topos e fundos)"""
    points = []
    for i in range(lookback, len(df) - lookback):
        is_high = True
        is_low = True
        for j in range(1, lookback + 1):
            if df["h"].iloc[i] <= df["h"].iloc[i - j] or df["h"].iloc[i] <= df["h"].iloc[i + j]:
                is_high = False
            if df["l"].iloc[i] >= df["l"].iloc[i - j] or df["l"].iloc[i] >= df["l"].iloc[i + j]:
                is_low = False
        if is_high:
            points.append({"type": "high", "price": df["h"].iloc[i], "index": i})
        if is_low:
            points.append({"type": "low", "price": df["l"].iloc[i], "index": i})
    return points

def detect_market_structure_advanced(df: pd.DataFrame) -> Dict:
    """Detecção avançada de estrutura de mercado"""
    if len(df) < 20:
        return {"trend": "consolidation", "strength": 50, "higherHighs": 0, "higherLows": 0, "lowerHighs": 0, "lowerLows": 0}
    
    swing_points = find_swing_points(df)
    swing_highs = [p["price"] for p in swing_points if p["type"] == "high"]
    swing_lows = [p["price"] for p in swing_points if p["type"] == "low"]
    
    hh = 0
    hl = 0
    lh = 0
    ll = 0
    
    for i in range(1, len(swing_highs)):
        if swing_highs[i] > swing_highs[i - 1]:
            hh += 1
        else:
            lh += 1
    
    for i in range(1, len(swing_lows)):
        if swing_lows[i] > swing_lows[i - 1]:
            hl += 1
        else:
            ll += 1
    
    if hh > lh and hl > ll:
        trend = "bullish"
        strength = min(100, ((hh + hl) / (hh + hl + lh + ll)) * 100)
    elif lh > hh and ll > hl:
        trend = "bearish"
        strength = min(100, ((lh + ll) / (hh + hl + lh + ll)) * 100)
    else:
        trend = "consolidation"
        strength = 50
    
    return {"trend": trend, "strength": strength, "higherHighs": hh, "higherLows": hl, "lowerHighs": lh, "lowerLows": ll}

def find_support_resistance(df: pd.DataFrame) -> List[Dict]:
    """Encontra níveis de suporte e resistência"""
    levels = []
    swing_points = find_swing_points(df, lookback=3)
    if len(swing_points) < 2:
        return []
    
    avg_range = df["h"].iloc[-20:].mean() - df["l"].iloc[-20:].mean()
    tolerance = avg_range * 0.5
    
    clusters = []
    for point in swing_points:
        merged = False
        for cluster in clusters:
            if abs(cluster["price"] - point["price"]) < tolerance:
                cluster["price"] = (cluster["price"] * cluster["touches"] + point["price"]) / (cluster["touches"] + 1)
                cluster["touches"] += 1
                merged = True
                break
        if not merged:
            clusters.append({"price": point["price"], "touches": 1})
    
    current_price = df["c"].iloc[-1]
    for cluster in clusters:
        if cluster["touches"] >= 2:
            levels.append({
                "price": cluster["price"],
                "type": "support" if cluster["price"] < current_price else "resistance",
                "strength": min(100, cluster["touches"] * 25),
                "touches": cluster["touches"]
            })
    
    return sorted(levels, key=lambda x: x["strength"], reverse=True)[:8]

def detect_reversal_candle(df: pd.DataFrame) -> Dict:
    """Detecta candles de reversão (martelo, engulfing, estrela cadente)"""
    if len(df) < 2:
        return {"detected": False, "type": "bullish"}
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    body = abs(last["c"] - last["o"])
    range_candle = last["h"] - last["l"]
    
    # Martelo / Pin bar (bullish)
    if last["c"] > last["o"] and (last["o"] - last["l"]) > body * 2 and range_candle > 0:
        return {"detected": True, "type": "bullish"}
    
    # Estrela cadente (bearish)
    if last["c"] < last["o"] and (last["h"] - last["o"]) > body * 2 and range_candle > 0:
        return {"detected": True, "type": "bearish"}
    
    # Engulfing bullish
    if prev["c"] < prev["o"] and last["c"] > last["o"] and last["c"] > prev["o"] and last["o"] < prev["c"]:
        return {"detected": True, "type": "bullish"}
    
    # Engulfing bearish
    if prev["c"] > prev["o"] and last["c"] < last["o"] and last["c"] < prev["o"] and last["o"] > prev["c"]:
        return {"detected": True, "type": "bearish"}
    
    return {"detected": False, "type": "bullish"}

def detect_whale_activity(df: pd.DataFrame) -> Dict:
    """Detecta atividade de baleias (picos de volume anormais)"""
    if len(df) < 20:
        return {"detected": False, "magnitude": 0}
    
    avg_vol = df["v"].iloc[-20:-1].mean()
    last_vol = df["v"].iloc[-1]
    avg_range = (df["h"].iloc[-20:-1] - df["l"].iloc[-20:-1]).mean()
    last_range = df["h"].iloc[-1] - df["l"].iloc[-1]
    
    if last_vol > avg_vol * 2.5:
        return {"detected": True, "type": "volume_spike", "magnitude": last_vol / avg_vol}
    
    if last_range > avg_range * 2:
        return {"detected": True, "type": "price_spike", "magnitude": last_range / avg_range}
    
    return {"detected": False, "magnitude": 0}

def detect_pullback_advanced(df: pd.DataFrame, structure: Dict) -> bool:
    """Detecção avançada de pullback"""
    if len(df) < 10:
        return False
    
    recent = df.iloc[-5:]
    
    if structure["trend"] == "bullish":
        pullback_down = all(recent["c"].iloc[i] <= recent["c"].iloc[i-1] for i in range(1, 3))
        bounce = recent["c"].iloc[-1] > recent["c"].iloc[-2]
        return pullback_down and bounce
    
    if structure["trend"] == "bearish":
        pullback_up = all(recent["c"].iloc[i] >= recent["c"].iloc[i-1] for i in range(1, 3))
        drop = recent["c"].iloc[-1] < recent["c"].iloc[-2]
        return pullback_up and drop
    
    return False

# ==================== FILTROS DO SEU BOT ====================
def anti_fake_breakout(df: pd.DataFrame) -> bool:
    if df.empty or len(df) < 3:
        return False
    last = df.iloc[-1]
    prev = df.iloc[-2]
    body = abs(last["c"] - last["o"])
    range_candle = last["h"] - last["l"]
    if range_candle == 0 or body < range_candle * 0.3:
        return False
    if (last["c"] > last["o"] and prev["c"] < prev["o"]) or (last["c"] < last["o"] and prev["c"] > prev["o"]):
        return False
    return True

def volume_force(df: pd.DataFrame, period: int = 20) -> bool:
    if df.empty or len(df) < period:
        return False
    avg_vol = df["v"].rolling(period).mean()
    return df["v"].iloc[-1] > avg_vol.iloc[-1] * 1.1

def is_lateral(df: pd.DataFrame, lookback: int = 20, threshold: float = 0.003) -> bool:
    if df.empty or len(df) < lookback:
        return True
    recent = df["c"].iloc[-lookback:]
    range_price = recent.max() - recent.min()
    return (range_price / df["c"].iloc[-1]) < threshold

def strong_candle(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    last = df.iloc[-1]
    body = abs(last["c"] - last["o"])
    range_candle = last["h"] - last["l"]
    return range_candle != 0 and body > range_candle * 0.4

# ==================== ESTRATÉGIA HÍBRIDA ====================

def hybrid_entry_signal(df: pd.DataFrame) -> Optional[Dict]:
    """Estratégia híbrida combinando seu bot + código TS"""
    if len(df) < 50:
        return None
    
    # Análises do seu bot original
    ema200 = ema(df)
    r = rsi(df)
    bb = bollinger_bands(df)
    macd_data = macd(df)
    
    if ema200.empty or r.empty or not bb or not macd_data:
        return None
    
    # Análises avançadas do código TS
    structure = detect_market_structure_advanced(df)
    reversal = detect_reversal_candle(df)
    whale = detect_whale_activity(df)
    levels = find_support_resistance(df)
    pullback = detect_pullback_advanced(df, structure)
    
    price_now = df["c"].iloc[-1]
    price_prev = df["c"].iloc[-2]
    price_5_ago = df["c"].iloc[-5] if len(df) >= 5 else price_now
    ema_now = ema200.iloc[-1]
    r_now = r.iloc[-1]
    r_prev = r.iloc[-2] if len(r) >= 2 else 50
    
    total_score = 0
    buy_signals = 0
    sell_signals = 0
    
    # Fator 1: Tendência (seu bot + avançado)
    if price_now > ema_now:
        buy_signals += 1
        total_score += 20
    elif price_now < ema_now:
        sell_signals += 1
        total_score += 20
    
    # Fator 2: Força da tendência (novo)
    if structure["trend"] == "bullish" and structure["strength"] > 55:
        buy_signals += 1
        total_score += 15
    elif structure["trend"] == "bearish" and structure["strength"] > 55:
        sell_signals += 1
        total_score += 15
    
    # Fator 3: Pullback (seu bot + avançado)
    pullback_original = price_5_ago > price_prev if price_now > ema_now else price_5_ago < price_prev
    if pullback or pullback_original:
        if price_now > ema_now:
            buy_signals += 1
        else:
            sell_signals += 1
        total_score += 15
    
    # Fator 4: RSI Reset (seu bot)
    rsi_reset_buy = r_prev < 45 and r_now > r_prev
    rsi_reset_sell = r_prev > 55 and r_now < r_prev
    if rsi_reset_buy and price_now > ema_now:
        buy_signals += 1
        total_score += 15
    elif rsi_reset_sell and price_now < ema_now:
        sell_signals += 1
        total_score += 15
    
    # Fator 5: Candle de reversão (novo)
    if reversal["detected"]:
        if reversal["type"] == "bullish" and price_now > ema_now:
            buy_signals += 1
            total_score += 15
        elif reversal["type"] == "bearish" and price_now < ema_now:
            sell_signals += 1
            total_score += 15
    
    # Fator 6: Baleias (novo)
    if whale["detected"]:
        total_score += 10
    
    # Fator 7: Próximo a suporte/resistência (novo)
    near_level = False
    atr_val = atr(df).iloc[-1] if not atr(df).empty else 0.001
    for level in levels[:3]:
        if abs(level["price"] - price_now) < atr_val * 1.5:
            near_level = True
            break
    if near_level:
        total_score += 10
    
    # Fator 8: Bollinger + MACD (seu bot)
    if price_now > ema_now and price_now < bb["upper"].iloc[-1] and macd_data["histogram"].iloc[-1] > 0:
        buy_signals += 1
        total_score += 15
    elif price_now < ema_now and price_now > bb["lower"].iloc[-1] and macd_data["histogram"].iloc[-1] < 0:
        sell_signals += 1
        total_score += 15
    
    # Fator 9: Momentum (seu bot)
    if price_now > price_prev and price_now > ema_now:
        buy_signals += 1
        total_score += 10
    elif price_now < price_prev and price_now < ema_now:
        sell_signals += 1
        total_score += 10
    
    # Fator 10: Anti-fake breakout (seu bot - exclusivo)
    if anti_fake_breakout(df):
        total_score += 10

    # Fator 11: Confirmação de volume na direção do sinal (melhoria)
    last_vol = df["v"].iloc[-1]
    avg_vol_5 = df["v"].iloc[-6:-1].mean() if len(df) >= 6 else last_vol
    if avg_vol_5 > 0:
        vol_ratio = last_vol / avg_vol_5
        if vol_ratio > 1.3:  # Volume 30%+ acima da média
            if price_now > ema_now:
                buy_signals += 1
            else:
                sell_signals += 1
            total_score += 10

    # Fator 12: RSI em zona favorável (melhoria - evita entrar no topo/fundo do RSI)
    if price_now > ema_now and 40 <= r_now <= 65:
        buy_signals += 1
        total_score += 8
    elif price_now < ema_now and 35 <= r_now <= 60:
        sell_signals += 1
        total_score += 8

    # Decisão final baseada nos scores
    min_score = 50  # Aumentado de 45 para 50 com novos fatores
    min_signals = 3  # Aumentado de 2 para 3 para maior qualidade
    
    if total_score >= min_score and buy_signals >= min_signals:
        return {"signal": "UP", "score": total_score, "justification": generate_justification(structure, reversal, whale, near_level, total_score)}
    elif total_score >= min_score and sell_signals >= min_signals:
        return {"signal": "DOWN", "score": total_score, "justification": generate_justification(structure, reversal, whale, near_level, total_score)}
    
    return None

def generate_justification(structure: Dict, reversal: Dict, whale: Dict, near_level: bool, score: int) -> str:
    """Gera justificativa para o sinal"""
    justifications = []
    
    if structure["trend"] == "bullish":
        justifications.append(f"tendência de alta confirmada (força: {structure['strength']:.0f}%)")
    elif structure["trend"] == "bearish":
        justifications.append(f"tendência de baixa confirmada (força: {structure['strength']:.0f}%)")
    
    if reversal["detected"]:
        justifications.append(f"candle de reversão {reversal['type']} detectado")
    
    if whale["detected"]:
        justifications.append(f"atividade anormal de volume detectada (baleias)")
    
    if near_level:
        justifications.append("preço próximo a nível importante de suporte/resistência")
    
    justifications.append(f"score total: {score}")
    
    return f"Sinal gerado com base em: {'. '.join(justifications)}."

# ==================== GESTÃO DE RISCO ADAPTATIVA ====================
class AdaptiveRiskManager:
    def __init__(self):
        self.initial_balance = get_account_balance()
        self.min_balance = self.initial_balance * 0.8 if self.initial_balance > 0 else 0
        self.max_risk = RISK_MAX
        self.min_risk = RISK_MIN
    
    def get_risk_percentage(self, current_balance: float) -> float:
        if current_balance <= 0:
            return self.min_risk
        drawdown = (self.initial_balance - current_balance) / self.initial_balance if self.initial_balance > 0 else 0
        if current_balance <= self.min_balance or drawdown > 0.1:
            return self.min_risk
        if drawdown > 0.05:
            return self.min_risk + 0.002
        profit_ratio = max(0, (current_balance - self.initial_balance) / self.initial_balance) if self.initial_balance > 0 else 0
        extra_risk = min(profit_ratio * 0.1, 0.005)
        return min(self.min_risk + extra_risk, self.max_risk)

def validate_capital_for_trade(symbol: str, current_balance: float) -> Tuple[bool, str]:
    if not PROTECT_CAPITAL:
        return True, "Proteção desativada"
    min_notional = symbol_filters.get(symbol, {}).get("min_notional", 10)
    required_capital = min_notional * 2
    if current_balance < required_capital:
        return False, f"Saldo ${current_balance:.2f} < mínimo recomendado ${required_capital:.2f}"
    return True, "OK"

def quick_backtest(symbol: str, lookback_days: int = 7) -> Dict:
    try:
        df = get_candles(symbol, "1h", limit=lookback_days * 24)
        if df.empty or len(df) < 100:
            return {"confidence": 0, "message": "Dados insuficientes"}
        signals = 0
        for i in range(100, len(df)):
            signal = hybrid_entry_signal(df.iloc[i-100:i])
            if signal:
                signals += 1
        expected_signals = lookback_days * 0.5
        confidence = min(signals / expected_signals, 1.0) * 100 if expected_signals > 0 else 0
        return {"confidence": round(confidence, 1), "signals_count": signals, "message": f"{signals} sinais em {lookback_days} dias"}
    except Exception as e:
        return {"confidence": 0, "message": str(e)}

# ==================== VARIÁVEIS PARA O SCANNER ====================
scanner_data = {
    "candidates": [],
    "last_update": 0
}

# ==================== BOT CORE ====================
lock = threading.Lock()
bot_on = False
positions = {}
daily_loss = 0.0
start_balance = 0.0

def sync_positions():
    global positions
    try:
        data = safe_binance_request(client.futures_position_information)
        with lock:
            positions.clear()
            for p in data:
                amt = float(p["positionAmt"])
                if amt != 0:
                    side = "UP" if amt > 0 else "DOWN"
                    positions[p["symbol"]] = {
                        "side": side,
                        "entry": float(p["entryPrice"]),
                        "qty": abs(amt),
                        "entry_time": datetime.now().isoformat()
                    }
        log("Posições sincronizadas", level='success')
    except Exception as e:
        log(f"Erro na sincronização: {e}", level='error')

def calculate_size(symbol: str, bal: float, entry: float, stop: float, risk_pct: float) -> float:
    if stop == entry:
        return 0.0
    risk_amount = (bal * risk_pct) / LEVERAGE
    dist = abs(entry - stop)
    if dist == 0:
        return 0.0
    return adjust_qty(symbol, risk_amount / dist)

def execute_trade_optimized(symbol: str, side: str, signal_info: Dict = None):
    """Executa trade com logs detalhados de rejeição"""
    global positions
    with lock:
        if symbol in positions:
            log(f"🔍 Já existe posição para {symbol}, ignorando novo sinal", level='debug')
            return
        
        try:
            bal = get_account_balance()
            log(f"🔍 Verificando trade {symbol} {side} | Saldo: ${bal:.2f}", level='debug')
            
            # 1. Verifica capital mínimo
            capital_ok, msg = validate_capital_for_trade(symbol, bal)
            if not capital_ok:
                log(f"🚫 Trade NÃO executado: {symbol} {side}", level='reject')
                log(f"   Motivo: Capital insuficiente - {msg}", level='reject')
                return
            
            # 2. Backtest
            backtest = quick_backtest(symbol, 7)
            if backtest["confidence"] < MIN_BACKTEST_CONFIDENCE:
                log(f"🚫 Trade NÃO executado: {symbol} {side}", level='reject')
                log(f"   Motivo: Confiança do backtest abaixo do mínimo ({backtest['confidence']:.1f}% < {MIN_BACKTEST_CONFIDENCE}%)", level='reject')
                return
            
            current_price = get_current_price(symbol)
            if not current_price:
                log(f"🚫 Trade NÃO executado: {symbol} {side}", level='reject')
                log(f"   Motivo: Não foi possível obter o preço atual", level='reject')
                return
            
            df = get_candles(symbol, "5m")
            if df.empty:
                log(f"🚫 Trade NÃO executado: {symbol} {side}", level='reject')
                log(f"   Motivo: Não foi possível obter dados de candle", level='reject')
                return
            
            atr_val = atr(df).iloc[-1] if not atr(df).empty else 0
            volatility = atr_val / current_price if current_price else 0
            
            # 3. Verifica volatilidade mínima
            min_volatility = 0.0005
            if volatility < min_volatility:
                log(f"🚫 Trade NÃO executado: {symbol} {side}", level='reject')
                log(f"   Motivo: Volatilidade insuficiente ({volatility:.6f} < {min_volatility})", level='reject')
                return
            
            risk_manager = AdaptiveRiskManager()
            dynamic_risk = risk_manager.get_risk_percentage(bal)
            
            if side == "UP":
                stop_loss_price = adjust_price(symbol, current_price - (atr_val * 1.2))
                take_profit_price = adjust_price(symbol, current_price + (atr_val * RR))
                order_side = "BUY"
                exit_side = "SELL"
            else:
                stop_loss_price = adjust_price(symbol, current_price + (atr_val * 1.2))
                take_profit_price = adjust_price(symbol, current_price - (atr_val * RR))
                order_side = "SELL"
                exit_side = "BUY"
            
            qty = calculate_size(symbol, bal, current_price, stop_loss_price, dynamic_risk)
            
            if qty <= 0:
                log(f"🚫 Trade NÃO executado: {symbol} {side}", level='reject')
                log(f"   Motivo: Quantidade calculada é zero ou negativa ({qty:.8f})", level='reject')
                return
            
            # 4. Valida ordem na Binance
            is_valid, message = validate_order(symbol, qty, current_price)
            if not is_valid:
                log(f"🚫 Trade NÃO executado: {symbol} {side}", level='reject')
                log(f"   Motivo: Ordem inválida - {message}", level='reject')
                return
            
            # EXECUTA O TRADE
            log(f"🔍 Executando ordem na Binance para {symbol} {order_side} {qty} @ {current_price}", level='debug')
            safe_binance_request(client.futures_change_leverage, symbol=symbol, leverage=LEVERAGE)
            safe_binance_request(client.futures_create_order, symbol=symbol, side=order_side, type="MARKET", quantity=qty)
            safe_binance_request(client.futures_create_order, symbol=symbol, side=exit_side, type="STOP_MARKET", stopPrice=stop_loss_price, closePosition=True)
            safe_binance_request(client.futures_create_order, symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET", stopPrice=take_profit_price, closePosition=True)
            
            position_data = {
                "side": side, "entry": current_price, "qty": qty,
                "entry_time": datetime.now().isoformat(),
                "stop_loss": stop_loss_price, "take_profit": take_profit_price,
                "risk_used": dynamic_risk, "backtest_confidence": backtest["confidence"]
            }
            positions[symbol] = position_data
            save_position(symbol, position_data)
            
            justification = signal_info.get("justification", "") if signal_info else ""
            log(f"✅ Trade EXECUTADO: {symbol} {side} | Risco: {dynamic_risk*100:.1f}% | Confiança: {backtest['confidence']}% | Justificativa: {justification}", level='trade')
            
        except Exception as e:
            log(f"❌ Erro na execução do trade {symbol}: {e}", level='error')
            import traceback
            traceback.print_exc()

def manage_positions():
    global positions, daily_loss
    to_remove = []
    with lock:
        for symbol, pos in list(positions.items()):
            try:
                current_price = get_current_price(symbol)
                if not current_price:
                    continue
                entry = pos["entry"]
                qty = pos["qty"]
                side = pos["side"]
                if side == "UP":
                    if current_price > pos.get("highest_price", entry):
                        pos["highest_price"] = current_price
                else:
                    if current_price < pos.get("lowest_price", entry):
                        pos["lowest_price"] = current_price
                pnl = (current_price - entry) * qty if side == "UP" else (entry - current_price) * qty
                pnl_pct = ((current_price - entry) / entry) * 100 if side == "UP" else ((entry - current_price) / entry) * 100
                new_stop = None
                if side == "UP":
                    if pnl_pct > 0.5 and not pos.get("trailing_activated"):
                        pos["trailing_activated"] = True
                        update_position(symbol, {"trailing_activated": True})
                    if pos.get("trailing_activated"):
                        new_stop = pos["highest_price"] * 0.997
                else:
                    if pnl_pct > 0.5 and not pos.get("trailing_activated"):
                        pos["trailing_activated"] = True
                        update_position(symbol, {"trailing_activated": True})
                    if pos.get("trailing_activated"):
                        new_stop = pos["lowest_price"] * 1.003
                close_pos = False
                reason = ""
                side_close = ""
                if side == "UP":
                    if current_price <= pos.get("stop_loss", 0):
                        close_pos, reason, side_close = True, "stop_loss", "SELL"
                    elif new_stop and current_price < new_stop:
                        close_pos, reason, side_close = True, "trailing_stop", "SELL"
                    elif current_price >= pos.get("take_profit", float('inf')):
                        close_pos, reason, side_close = True, "take_profit", "SELL"
                else:
                    if current_price >= pos.get("stop_loss", float('inf')):
                        close_pos, reason, side_close = True, "stop_loss", "BUY"
                    elif new_stop and current_price > new_stop:
                        close_pos, reason, side_close = True, "trailing_stop", "BUY"
                    elif current_price <= pos.get("take_profit", 0):
                        close_pos, reason, side_close = True, "take_profit", "BUY"
                if close_pos:
                    safe_binance_request(client.futures_create_order, symbol=symbol, side=side_close, type="MARKET", quantity=qty, reduceOnly=True)
                    log(f"Posição fechada: {symbol} - {reason} - PNL: {pnl:.2f} USDT ({pnl_pct:.2f}%)", level='trade' if pnl > 0 else 'risk')
                    if pnl < 0:
                        daily_loss += abs(pnl)
                        save_state("daily_loss", daily_loss)
                    trade_data = {
                        "symbol": symbol, "side": side, "entry_price": entry, "exit_price": current_price,
                        "quantity": qty, "pnl": pnl, "pnl_percentage": pnl_pct,
                        "entry_time": pos["entry_time"], "exit_time": datetime.now().isoformat(),
                        "reason": reason, "risk_used": pos.get("risk_used", RISK)
                    }
                    save_trade_to_history(trade_data)
                    update_daily_metrics(datetime.now().strftime("%Y-%m-%d"), pnl, pnl > 0)
                    to_remove.append(symbol)
                    delete_position(symbol)
            except Exception as e:
                log(f"Erro no gerenciamento de {symbol}: {e}", level='error')
        for symbol in to_remove:
            if symbol in positions:
                del positions[symbol]

def find_candidate_pairs() -> List[str]:
    global scanner_data
    try:
        data = safe_binance_request(client.futures_ticker)
        candidates = []
        all_scores = []
        
        for x in data:
            symbol = x["symbol"]
            if not symbol.endswith("USDT") or symbol not in symbol_filters:
                continue
            
            price = float(x["lastPrice"])
            volume = float(x["quoteVolume"])
            
            if price > MAX_PRICE:
                continue
            
            if volume < 3_000_000:
                continue
            
            df = get_candles(symbol, "5m", limit=100)
            if df.empty:
                continue
            
            score, trend_val = calculate_score(symbol)
            
            volume_score = min(volume / 50_000_000 * 10, 30)
            atr_val = atr(df).iloc[-1] if not atr(df).empty else 0
            volatility_score = (atr_val / price) * 1000 if price != 0 else 0
            total_score = score + volume_score + min(volatility_score, 30)
            
            all_scores.append({
                "symbol": symbol,
                "score": round(total_score, 2),
                "price": round(price, 4),
                "trend": trend_val,
                "volume": round(volume / 1_000_000, 2),
                "volatility": round(volatility_score, 2)
            })
            
            candidates.append((symbol, total_score))
        
        all_scores.sort(key=lambda x: x["score"], reverse=True)
        scanner_data["candidates"] = all_scores[:50]
        scanner_data["last_update"] = time.time()
        
        candidates.sort(key=lambda x: x[1], reverse=True)
        if candidates:
            log(f"Top candidatos: {candidates[:3]}", level='debug')
        
        return [x[0] for x in candidates[:10]]
        
    except Exception as e:
        log(f"Erro ao buscar pares: {e}", level='error')
        return []

def bot_loop():
    global bot_on, daily_loss, positions, start_balance
    log("Nostradamus Bot iniciado - Versão Híbrida 3.2.0", level='success')
    sync_positions()
    start_balance = get_account_balance()
    save_state("start_balance", start_balance)
    last_sync = time.time()
    while bot_on:
        try:
            bal = get_account_balance()
            if bal <= 0:
                log("Saldo zerado! Parando bot.", level='risk')
                bot_on = False
                break
            if daily_loss > start_balance * DAILY_LOSS_LIMIT:
                log(f"Stop diário atingido! Perda: {daily_loss:.2f}", level='risk')
                bot_on = False
                break
            if time.time() - last_sync > 300:
                sync_positions()
                last_sync = time.time()
            with lock:
                if len(positions) < MAX_TRADES:
                    for sym in find_candidate_pairs():
                        if sym in positions:
                            continue
                        df = get_candles(sym, "5m")
                        if df.empty:
                            continue
                        
                        signal_info = hybrid_entry_signal(df)
                        if not signal_info:
                            continue
                        
                        sig = signal_info["signal"]
                        
                        # Log detalhado dos filtros
                        lateral_check = is_lateral(df)
                        anti_fake_check = anti_fake_breakout(df)
                        strong_candle_check = strong_candle(df)
                        volume_force_check = volume_force(df)
                        
                        log(f"🔍 Filtros para {sym} {sig}: Lateral={lateral_check}, AntiFake={anti_fake_check}, StrongCandle={strong_candle_check}, VolumeForce={volume_force_check}", level='debug')
                        
                        if lateral_check:
                            log(f"   ❌ Filtro lateral reprovou", level='debug')
                            continue
                        
                        if not anti_fake_check:
                            log(f"   ❌ Filtro anti-fake reprovou", level='debug')
                            continue
                        
                        if not strong_candle_check:
                            log(f"   ❌ Filtro candle forte reprovou", level='debug')
                            continue
                        
                        if not volume_force_check:
                            log(f"   ❌ Filtro volume reprovou", level='debug')
                            continue
                        
                        log(f"💰 Sinal encontrado: {sym} {sig} | Score: {signal_info.get('score', 0)} | Justificativa: {signal_info.get('justification', '')}", level='trade')
                        execute_trade_optimized(sym, sig, signal_info)
                        if sym in positions:
                            break
            manage_positions()
            save_state("daily_loss", daily_loss)
        except Exception as e:
            log(f"Erro no loop: {e}", level='error')
        time.sleep(INTERVAL)
    log("Bot parado", level='warning')

# ==================== ENDPOINT DE TESTE MANUAL ====================
@app.get("/api/test_trade/{symbol}/{side}")
async def test_trade(symbol: str, side: str):
    """Endpoint para testar execução de trade manualmente"""
    try:
        # Verifica se side é válido
        if side.upper() not in ["UP", "DOWN"]:
            return {"status": "erro", "error": "Side deve ser UP ou DOWN"}
        
        signal_info = {"signal": side.upper(), "score": 100, "justification": "Teste manual via endpoint"}
        execute_trade_optimized(symbol.upper(), side.upper(), signal_info)
        return {"status": "teste iniciado", "symbol": symbol.upper(), "side": side.upper(), "message": "Verifique os logs para o resultado"}
    except Exception as e:
        return {"status": "erro", "error": str(e)}

# ==================== AUTENTICAÇÃO ====================
JWT_SECRET = os.getenv("JWT_SECRET", "chave-secreta-padrao")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

class AuthHandler:
    @staticmethod
    def verify_password(password: str) -> bool:
        if not ADMIN_PASSWORD_HASH:
            return False
        try:
            import bcrypt
            return bcrypt.checkpw(password.encode(), ADMIN_PASSWORD_HASH.encode())
        except Exception:
            # fallback sha256 para compatibilidade com hashes antigos
            return hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH

    @staticmethod
    def create_token() -> str:
        payload = {"exp": datetime.utcnow() + timedelta(hours=24), "iat": datetime.utcnow(), "role": "admin"}
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    @staticmethod
    def verify_token(token: str) -> bool:
        try:
            jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            return True
        except:
            return False

@app.post("/auth/login")
async def login(request: Request):
    try:
        body = await request.json()
        password = body.get("password")
        if not password:
            raise HTTPException(400, "Senha obrigatória")
        if AuthHandler.verify_password(password):
            return {"token": AuthHandler.create_token(), "expires_in": 86400}
        raise HTTPException(401, "Senha inválida")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/auth/verify")
async def verify_token(request: Request):
    try:
        body = await request.json()
        token = body.get("token")
        if not token:
            raise HTTPException(400, "Token obrigatório")
        return {"valid": AuthHandler.verify_token(token)}
    except Exception as e:
        raise HTTPException(500, str(e))

# ==================== API ENDPOINTS ====================
@app.get("/")
async def root():
    dashboard_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(dashboard_path):
        return FileResponse(dashboard_path)
    return {"status": "online", "version": "3.2.0", "name": "Nostradamus Trading Bot", "testnet": BINANCE_TESTNET, "timestamp": datetime.now().isoformat()}

@app.get("/api/info")
async def api_info():
    return {"status": "online", "version": "3.2.0", "name": "Nostradamus Trading Bot - Versão Híbrida", "timestamp": datetime.now().isoformat()}

@app.get("/api/status")
async def get_status():
    global bot_on, daily_loss, start_balance
    bal = get_account_balance()
    pos_list = []
    for sym, data in positions.items():
        curr = get_current_price(sym)
        pnl = None
        if curr:
            if data["side"] == "UP":
                pnl = (curr - data["entry"]) * data["qty"]
            else:
                pnl = (data["entry"] - curr) * data["qty"]
        pos_list.append({
            "symbol": sym, "side": data["side"], "entry": round(data["entry"], 4), "qty": round(data["qty"], 4),
            "current_price": round(curr, 4) if curr else None, "pnl": round(pnl, 2) if pnl else None,
            "risk_used": data.get("risk_used", RISK) * 100, "entry_time": data.get("entry_time")
        })
    with get_db_connection() as conn:
        trades = conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]
        wins = conn.execute("SELECT COUNT(*) FROM trade_history WHERE pnl > 0").fetchone()[0]
        total_pnl = conn.execute("SELECT SUM(pnl) FROM trade_history").fetchone()[0] or 0
        win_rate = (wins / trades * 100) if trades > 0 else 0
        profit_factor = (conn.execute("SELECT SUM(pnl) FROM trade_history WHERE pnl > 0").fetchone()[0] or 0) / \
                        abs(conn.execute("SELECT SUM(pnl) FROM trade_history WHERE pnl < 0").fetchone()[0] or 1) if trades > 0 else 0
    metrics = {
        "trades": trades, "wins": wins, "losses": trades - wins,
        "win_rate": round(win_rate, 2), "profit_factor": round(profit_factor, 2) if profit_factor else 0,
        "total_pnl": round(total_pnl, 2), "daily_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
        "consecutive_wins": 0, "consecutive_losses": 0, "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        "uptime_hours": 0, "sharpe_ratio": 0.0
    }
    return {
        "running": bot_on, "testnet": BINANCE_TESTNET, "positions": pos_list, "daily_loss": round(daily_loss, 2),
        "daily_loss_limit": DAILY_LOSS_LIMIT,
        "daily_loss_percentage": round((daily_loss / bal) * 100, 2) if bal > 0 else 0,
        "current_balance": round(bal, 2), "start_balance": round(start_balance, 2),
        "total_pnl": round(bal - start_balance, 2),
        "config": {
            "leverage": LEVERAGE, "risk": RISK, "risk_max": RISK_MAX, "risk_min": RISK_MIN,
            "rr_ratio": RR, "max_trades": MAX_TRADES, "daily_loss_limit": DAILY_LOSS_LIMIT * 100,
            "min_backtest_confidence": MIN_BACKTEST_CONFIDENCE, "protect_capital": PROTECT_CAPITAL,
            "max_price": MAX_PRICE
        },
        "metrics": metrics
    }

@app.get("/api/scanner")
async def get_scanner():
    return {
        "candidates": scanner_data["candidates"],
        "last_update": scanner_data["last_update"],
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/bot/start")
async def start_bot():
    global bot_on
    if bot_on:
        return {"status": "already_running"}
    bot_on = True
    save_state("bot_on", True)
    threading.Thread(target=bot_loop, daemon=True).start()
    return {"status": "started"}

@app.post("/api/bot/stop")
async def stop_bot():
    global bot_on
    bot_on = False
    save_state("bot_on", False)
    return {"status": "stopped"}

@app.get("/api/balance")
async def get_balance():
    return {"balance": round(get_account_balance(), 2)}

@app.get("/api/history")
async def get_history(limit: int = 100):
    with get_db_connection() as conn:
        return {"trades": [dict(row) for row in conn.execute("SELECT * FROM trade_history ORDER BY exit_time DESC LIMIT ?", (limit,)).fetchall()]}

@app.get("/api/metrics")
async def get_metrics():
    with get_db_connection() as conn:
        trades = conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]
        wins = conn.execute("SELECT COUNT(*) FROM trade_history WHERE pnl > 0").fetchone()[0]
        total_pnl = conn.execute("SELECT SUM(pnl) FROM trade_history").fetchone()[0] or 0
        win_rate = (wins / trades * 100) if trades > 0 else 0
        profit_factor = (conn.execute("SELECT SUM(pnl) FROM trade_history WHERE pnl > 0").fetchone()[0] or 0) / \
                        abs(conn.execute("SELECT SUM(pnl) FROM trade_history WHERE pnl < 0").fetchone()[0] or 1) if trades > 0 else 0
        return {"trades": trades, "wins": wins, "losses": trades - wins, "win_rate": round(win_rate, 2), "profit_factor": round(profit_factor, 2), "total_pnl": round(total_pnl, 2)}

# ==================== STARTUP ====================
# lock, bot_on, positions, daily_loss, start_balance já declarados no BOT CORE
bot_on = load_state("bot_on", False)
positions = load_positions()
daily_loss = load_state("daily_loss", 0.0)
start_balance = load_state("start_balance", 0.0)

@app.on_event("startup")
async def startup_event():
    log("Nostradamus Bot iniciado - Versão Híbrida 3.2.0", level='success')
    if bot_on:
        log("Bot estava ativo. Reiniciando...", level='info')
        threading.Thread(target=bot_loop, daemon=True).start()
    else:
        log("Bot aguardando comando para iniciar", level='info')

@app.on_event("shutdown")
async def shutdown_event():
    log("Servidor FastAPI encerrando", level='warning')
    global bot_on
    bot_on = False
    save_state("bot_on", False)
    
@app.get("/teste")
async def teste():
    return {"status": "ok", "message": "Servidor funcionando"}

@app.get("/login")
async def login_page():
    path = os.path.join(os.path.dirname(__file__), "static", "login.html")
    if os.path.exists(path):
        return FileResponse(path)
    return HTMLResponse("<h1>Login page not found</h1>", status_code=404)
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
