"""
NOSTRADAMUS TRADING BOT v4.2.1 - MANUS FIX
CORRIGIDO: Cálculo de Position Sizing, Notional Mínimo e Execução para Bancas Pequenas
"""
import os, threading, time, math, sqlite3, logging, json, pickle, warnings
warnings.filterwarnings('ignore')
from datetime import datetime, timedelta
from collections import deque
import contextlib
from typing import Optional, Dict, List, Tuple, Any
import hmac, hashlib
import numpy as np
import pandas as pd
import requests
import jwt, bcrypt
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

def log(msg, level='info'):
    em = {'info':'ℹ️','warning':'⚠️','error':'❌','debug':'🔍','success':'✅','trade':'💰','risk':'🛡️','reject':'🚫','ai':'🧠'}
    m = f"{em.get(level,'🤖')} {msg}"
    if level == 'error': logger.error(m)
    elif level in ('warning','risk','reject'): logger.warning(m)
    else: logger.info(m)

# ==================== CONFIG ====================
LEVERAGE=20; RISK=0.03; RR=2.0; MAX_TRADES=1; DAILY_LOSS_LIMIT=0.05
INTERVAL=10; RISK_MAX=0.05; RISK_MIN=0.02; MIN_BACKTEST_CONFIDENCE=20.0
PROTECT_CAPITAL=True; MAX_PRICE=20.0; AI_MIN_CONFIDENCE=40.0
PARTIAL_TP_ENABLED=True; PYRAMID_ENABLED=True

def validate_config():
    global LEVERAGE,RISK,RR,MAX_TRADES,DAILY_LOSS_LIMIT,INTERVAL,RISK_MAX,RISK_MIN
    global MIN_BACKTEST_CONFIDENCE,PROTECT_CAPITAL,MAX_PRICE,AI_MIN_CONFIDENCE,PARTIAL_TP_ENABLED,PYRAMID_ENABLED
    missing = [v for v in ["BINANCE_API_KEY","BINANCE_SECRET_KEY"] if not os.getenv(v)]
    if missing: raise ValueError(f"Variáveis faltando: {missing}")
    LEVERAGE=int(os.getenv("LEVERAGE","2")); RISK=float(os.getenv("RISK","0.01"))
    RR=float(os.getenv("RR","2.0")); MAX_TRADES=int(os.getenv("MAX_TRADES","2"))
    DAILY_LOSS_LIMIT=float(os.getenv("DAILY_LOSS_LIMIT","0.03")); INTERVAL=int(os.getenv("INTERVAL","10"))
    RISK_MAX=float(os.getenv("RISK_MAX","0.02")); RISK_MIN=float(os.getenv("RISK_MIN","0.005"))
    MIN_BACKTEST_CONFIDENCE=float(os.getenv("MIN_BACKTEST_CONFIDENCE","20"))
    PROTECT_CAPITAL=os.getenv("PROTECT_CAPITAL","true").lower()=="true"
    MAX_PRICE=float(os.getenv("MAX_PRICE","20")); AI_MIN_CONFIDENCE=float(os.getenv("AI_MIN_CONFIDENCE","40"))
    PARTIAL_TP_ENABLED=os.getenv("PARTIAL_TP","true").lower()=="true"
    PYRAMID_ENABLED=os.getenv("PYRAMID","true").lower()=="true"
    log("Configuração v4.2 validada com sucesso", level='success')

# ==================== APP ====================
app = FastAPI(title="Nostradamus v4.2.1 - Manus Fix", version="4.2.1")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ==================== RATE LIMITER ====================
class RateLimiter:
    def __init__(self, max_calls, period):
        self.max_calls=max_calls; self.period=period; self.calls=deque(); self._lock=threading.Lock()
    def wait_if_needed(self):
        with self._lock:
            now=time.time()
            while self.calls and self.calls[0] < now-self.period: self.calls.popleft()
            if len(self.calls)>=self.max_calls:
                st=self.calls[0]+self.period-now
                if st>0: time.sleep(st)
            self.calls.append(now)

binance_rl = RateLimiter(10,1)

# ==================== BINANCE ====================
BINANCE_API_KEY=os.getenv("BINANCE_API_KEY"); BINANCE_SECRET_KEY=os.getenv("BINANCE_SECRET_KEY")
BINANCE_TESTNET=os.getenv("BINANCE_TESTNET","false").lower()=="true"
BINANCE_DEMO=os.getenv("BINANCE_DEMO","false").lower()=="true"
validate_config()

if BINANCE_DEMO:
    client=Client(BINANCE_API_KEY,BINANCE_SECRET_KEY,testnet=True)
    client.FUTURES_URL="https://testnet.binancefuture.com/fapi"
    client.API_URL="https://testnet.binance.vision/api"
    log("🎮 MODO DEMO TRADING ATIVADO — dinheiro fictício", level='warning')
elif BINANCE_TESTNET:
    client=Client(BINANCE_API_KEY,BINANCE_SECRET_KEY,testnet=True)
    client.FUTURES_URL="https://testnet.binancefuture.com/fapi"
    log("🧪 MODO TESTNET ATIVADO — dinheiro fictício", level='warning')
else:
    client=Client(BINANCE_API_KEY,BINANCE_SECRET_KEY)
    log("💰 MODO PRODUÇÃO — conta real", level='info')

# ==================== DATABASE ====================
DB_DIR=os.getenv("DB_PATH","/app/data"); os.makedirs(DB_DIR,exist_ok=True)
DB_FILE=os.path.join(DB_DIR,"nostradamus_v4.db")

@contextlib.contextmanager
def get_db():
    conn=sqlite3.connect(DB_FILE); conn.row_factory=sqlite3.Row
    try: yield conn; conn.commit()
    except: conn.rollback(); raise
    finally: conn.close()

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY, side TEXT, entry REAL, qty REAL, entry_time TEXT,
            stop_loss REAL, take_profit REAL, tp_partial REAL,
            partial_tp_done INTEGER DEFAULT 0, pyramid_count INTEGER DEFAULT 0,
            trailing_activated INTEGER DEFAULT 0, highest_price REAL, lowest_price REAL,
            risk_used REAL, ai_confidence REAL, score INTEGER
        );
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, side TEXT, entry_price REAL, exit_price REAL,
            quantity REAL, pnl REAL, pnl_percentage REAL,
            entry_time TEXT, exit_time TEXT, reason TEXT,
            risk_used REAL, ai_confidence REAL, score INTEGER
        );
        CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY, trades INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0, total_pnl REAL DEFAULT 0,
            start_balance REAL, end_balance REAL
        );
        CREATE TABLE IF NOT EXISTS ai_training_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, features TEXT, outcome INTEGER, pnl REAL, created_at TEXT
        );
        """)
        log("Banco v4.2 inicializado", level='success')

init_db()

def save_state(k,v):
    with get_db() as conn: conn.execute("INSERT OR REPLACE INTO bot_state VALUES(?,?,?)",(k,str(v),datetime.now().isoformat()))

def load_state(k,default):
    with get_db() as conn: r=conn.execute("SELECT value FROM bot_state WHERE key=?",(k,)).fetchone()
    if r:
        try:
            if isinstance(default,bool): return r[0].lower()=='true'
            if isinstance(default,(int,float)): return type(default)(r[0])
            return r[0]
        except: return default
    return default

def save_position(symbol,data):
    with get_db() as conn:
        conn.execute("""INSERT OR REPLACE INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            symbol,data['side'],data['entry'],data['qty'],
            data.get('entry_time',datetime.now().isoformat()),
            data.get('stop_loss'),data.get('take_profit'),data.get('tp_partial'),
            data.get('partial_tp_done',0),data.get('pyramid_count',0),
            data.get('trailing_activated',0),
            data.get('highest_price',data['entry']),data.get('lowest_price',data['entry']),
            data.get('risk_used',RISK),data.get('ai_confidence',0),data.get('score',0)
        ))

def delete_position(sym):
    with get_db() as conn: conn.execute("DELETE FROM positions WHERE symbol=?",(sym,))

def load_positions():
    with get_db() as conn: return {r['symbol']:dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()}

def save_trade(d):
    with get_db() as conn:
        conn.execute("""INSERT INTO trade_history
            (symbol,side,entry_price,exit_price,quantity,pnl,pnl_percentage,
             entry_time,exit_time,reason,risk_used,ai_confidence,score)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            d['symbol'],d['side'],d['entry_price'],d['exit_price'],
            d['quantity'],d['pnl'],d['pnl_pct'],
            d['entry_time'],d['exit_time'],d['reason'],
            d.get('risk_used',RISK),d.get('ai_confidence',0),d.get('score',0)
        ))

def save_ai_data(symbol,features,outcome,pnl):
    with get_db() as conn:
        conn.execute("INSERT INTO ai_training_data(symbol,features,outcome,pnl,created_at) VALUES(?,?,?,?,?)",
                     (symbol,json.dumps(features),outcome,pnl,datetime.now().isoformat()))

# ==================== BINANCE HELPERS ====================
@retry(stop=stop_after_attempt(5),wait=wait_exponential(multiplier=1,min=4,max=10),
       retry=retry_if_exception_type((BinanceAPIException,BinanceRequestException,requests.exceptions.RequestException)),
       before_sleep=before_sleep_log(logger,logging.WARNING))
def safe_req(func,*args,**kwargs):
    binance_rl.wait_if_needed()
    try: return func(*args,**kwargs)
    except BinanceAPIException as e:
        if e.code==-1003: time.sleep(60)
        raise

symbol_filters={}

def load_filters():
    global symbol_filters
    try:
        info=safe_req(client.futures_exchange_info)
        loaded=0
        for s in info["symbols"]:
            if not s["symbol"].endswith("USDT"): continue
            if s.get("status","") != "TRADING": continue
            if s.get("contractType","") not in ("PERPETUAL","","LINEAR"): continue
            sf={f["filterType"]:f for f in s["filters"]}
            if "LOT_SIZE" not in sf or "PRICE_FILTER" not in sf: continue
            ls=sf["LOT_SIZE"]; pf=sf["PRICE_FILTER"]; mn=sf.get("MIN_NOTIONAL",{})
            symbol_filters[s["symbol"]]={
                "step":float(ls["stepSize"]),"min_qty":float(ls["minQty"]),
                "max_qty":float(ls.get("maxQty",1e9)),"tick":float(pf["tickSize"]),
                "min_price":float(pf.get("minPrice",0)),"max_price":float(pf.get("maxPrice",1e9)),
                "min_notional":float(mn.get("notional",5)) if mn else 5
            }
            loaded+=1
        log(f"{loaded} pares USDT disponíveis para trading",level='success')
    except Exception as e: log(f"Erro filtros: {e}",level='error')

load_filters()

def adj_qty(symbol,qty):
    if symbol not in symbol_filters: return qty
    f=symbol_filters[symbol]; step=f["step"]
    prec=max(0,int(round(-math.log10(step)))) if step>0 else 0
    qty=math.floor(qty/step)*step
    return max(min(round(qty,prec),f["max_qty"]),f["min_qty"])

def adj_price(symbol,price):
    if symbol not in symbol_filters: return price
    f=symbol_filters[symbol]; tick=f["tick"]
    prec=max(0,int(round(-math.log10(tick)))) if tick>0 else 0
    price=round(price/tick)*tick
    return round(max(min(price,f["max_price"]),f["min_price"]),prec)

def get_price(symbol):
    try: return float(safe_req(client.futures_symbol_ticker,symbol=symbol)["price"])
    except: return None

def get_balance():
    try:
        for b in safe_req(client.futures_account_balance):
            if b["asset"]=="USDT": return float(b["balance"])
        return 0.0
    except: return 0.0

def get_candles(symbol,tf,limit=200):
    try:
        data=safe_req(client.futures_klines,symbol=symbol,interval=tf,limit=limit)
        df=pd.DataFrame(data,columns=["t","o","h","l","c","v","ct","qv","n","tb","tq","i"])
        for col in ["o","h","l","c","v","qv","tb","tq"]: df[col]=df[col].astype(float)
        df["t"]=pd.to_datetime(df["t"],unit='ms')
        return df
    except: return pd.DataFrame()

# ==================== INDICADORES v4.2 ====================
def calc_ema(series,span): return series.ewm(span=span,adjust=False).mean()

def calc_rsi(df,period=14):
    delta=df["c"].diff(); gain=delta.clip(lower=0).rolling(period).mean()
    loss=(-delta.clip(upper=0)).rolling(period).mean()
    return 100-(100/(1+gain/loss.replace(0,1e-10)))

def calc_atr(df,period=14):
    tr=pd.concat([df["h"]-df["l"],(df["h"]-df["c"].shift()).abs(),(df["l"]-df["c"].shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calc_adx(df, period=14):
    plus_dm = df["h"].diff(); minus_dm = df["l"].diff()
    plus_dm[plus_dm < 0] = 0; minus_dm[minus_dm > 0] = 0
    tr = pd.concat([df["h"]-df["l"], (df["h"]-df["c"].shift()).abs(), (df["l"]-df["c"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (abs(minus_dm.rolling(period).mean()) / atr)
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-10))
    return dx.rolling(period).mean()

def calc_macd(df):
    e12=calc_ema(df["c"],12); e26=calc_ema(df["c"],26); line=e12-e26
    signal=calc_ema(line,9); return line,signal,line-signal

def calc_bollinger(df,period=20,std=2):
    tp=(df["h"]+df["l"]+df["c"])/3; mid=tp.rolling(period).mean(); s=tp.rolling(period).std()
    return mid+s*std,mid,mid-s*std

def calc_ichimoku(df):
    h9=df["h"].rolling(9).max(); l9=df["l"].rolling(9).min()
    h26=df["h"].rolling(26).max(); l26=df["l"].rolling(26).min()
    h52=df["h"].rolling(52).max(); l52=df["l"].rolling(52).min()
    tenkan=(h9+l9)/2; kijun=(h26+l26)/2
    spanA=((tenkan+kijun)/2).shift(26); spanB=((h52+l52)/2).shift(26)
    return {"tenkan":tenkan,"kijun":kijun,"spanA":spanA,"spanB":spanB}

def ichimoku_signal(df):
    if len(df)<60: return 0,0
    ichi=calc_ichimoku(df)
    price=df["c"].iloc[-1]; tenkan=ichi["tenkan"].iloc[-1]; kijun=ichi["kijun"].iloc[-1]
    spanA=ichi["spanA"].iloc[-1]; spanB=ichi["spanB"].iloc[-1]
    if any(math.isnan(x) for x in [tenkan,kijun,spanA,spanB]): return 0,0
    cloud_top=max(spanA,spanB); cloud_bot=min(spanA,spanB)
    bull=0; bear=0
    if price>cloud_top: bull+=2
    elif price<cloud_bot: bear+=2
    if tenkan>kijun: bull+=1
    elif tenkan<kijun: bear+=1
    if spanA>spanB: bull+=1
    else: bear+=1
    score=bull-bear
    return (1 if score>=2 else -1 if score<=-2 else 0), abs(score)

def calc_vwap(df):
    tp=(df["h"]+df["l"]+df["c"])/3
    return (tp*df["v"]).cumsum()/df["v"].cumsum().replace(0,1e-10)

def vwap_signal(df):
    if len(df)<20: return 0
    vw=calc_vwap(df); price=df["c"].iloc[-1]; vw_now=vw.iloc[-1]
    if math.isnan(vw_now): return 0
    pct=(price-vw_now)/vw_now*100
    return 1 if pct>0.3 else -1 if pct<-0.3 else 0

def fibonacci_levels(df,lookback=50):
    if len(df)<lookback: return {}
    recent=df.iloc[-lookback:]; high=recent["h"].max(); low=recent["l"].min(); diff=high-low
    return {"high":high,"low":low,"levels":{f"{r:.3f}":high-diff*r for r in [0,0.236,0.382,0.5,0.618,0.786,1.0]}}

def fibonacci_signal(df):
    if len(df)<50: return 0,None
    fibs=fibonacci_levels(df)
    if not fibs: return 0,None
    price=df["c"].iloc[-1]; atr_v=calc_atr(df).iloc[-1] if not calc_atr(df).empty else price*0.01
    tol=atr_v*0.5
    for ratio in [0.382,0.5,0.618]:
        lp=fibs["levels"].get(f"{ratio:.3f}")
        if lp and abs(price-lp)<tol:
            return (1 if price<fibs["high"]*0.5 else -1),{"ratio":ratio,"price":lp}
    return 0,None

def market_structure(df):
    if len(df)<20: return {"trend":"side","strength":0}
    highs=df["h"].rolling(5).max(); lows=df["l"].rolling(5).min()
    hh=(highs.diff()>0).sum(); lh=(highs.diff()<0).sum()
    hl=(lows.diff()>0).sum(); ll=(lows.diff()<0).sum(); total=hh+lh+hl+ll
    if total==0: return {"trend":"side","strength":0}
    if hh>lh and hl>ll: return {"trend":"bullish","strength":min(100,(hh+hl)/total*100)}
    if lh>hh and ll>hl: return {"trend":"bearish","strength":min(100,(lh+ll)/total*100)}
    return {"trend":"side","strength":50}

def reversal_candle(df):
    if len(df)<2: return {"detected":False,"type":None}
    last=df.iloc[-1]; prev=df.iloc[-2]
    body=abs(last["c"]-last["o"]); rng=last["h"]-last["l"]
    if rng==0: return {"detected":False,"type":None}
    if last["c"]>last["o"] and (last["o"]-last["l"])>body*2: return {"detected":True,"type":"bullish"}
    if last["c"]<last["o"] and (last["h"]-last["o"])>body*2: return {"detected":True,"type":"bearish"}
    if prev["c"]<prev["o"] and last["c"]>last["o"] and last["c"]>prev["o"] and last["o"]<prev["c"]: return {"detected":True,"type":"bullish"}
    if prev["c"]>prev["o"] and last["c"]<last["o"] and last["c"]<prev["o"] and last["o"]>prev["c"]: return {"detected":True,"type":"bearish"}
    return {"detected":False,"type":None}

def whale_activity(df):
    if len(df)<20: return {"detected":False,"magnitude":0}
    avg_vol=df["v"].iloc[-20:-1].mean(); last_vol=df["v"].iloc[-1]
    if last_vol>avg_vol*2.5: return {"detected":True,"magnitude":last_vol/avg_vol}
    return {"detected":False,"magnitude":0}

def support_resistance(df):
    levels=[]; pts=[]
    for i in range(3,len(df)-3):
        if all(df["h"].iloc[i]>=df["h"].iloc[i-j] and df["h"].iloc[i]>=df["h"].iloc[i+j] for j in range(1,4)):
            pts.append({"type":"high","price":df["h"].iloc[i]})
        if all(df["l"].iloc[i]<=df["l"].iloc[i-j] and df["l"].iloc[i]<=df["l"].iloc[i+j] for j in range(1,4)):
            pts.append({"type":"low","price":df["l"].iloc[i]})
    if len(pts)<2: return []
    atr_v=calc_atr(df).iloc[-1] if not calc_atr(df).empty else 0.01; tol=atr_v*1.5; clusters=[]
    for p in pts:
        merged=False
        for c in clusters:
            if abs(c["price"]-p["price"])<tol: c["price"]=(c["price"]*c["t"]+p["price"])/(c["t"]+1); c["t"]+=1; merged=True; break
        if not merged: clusters.append({"price":p["price"],"t":1})
    price=df["c"].iloc[-1]
    for c in clusters:
        if c["t"]>=2: levels.append({"price":c["price"],"type":"support" if c["price"]<price else "resistance","strength":min(100,c["t"]*25)})
    return sorted(levels,key=lambda x: x["strength"],reverse=True)[:8]

# ==================== AI ENGINE v4.2 ====================
MODEL_FILE=os.path.join(DB_DIR,"ai_model_v4.pkl")

class AIEngine:
    def __init__(self):
        self.model=None; self.trained=False; self.training_count=0; self._load()

    def _load(self):
        try:
            if os.path.exists(MODEL_FILE):
                with open(MODEL_FILE,'rb') as f: self.model=pickle.load(f)
                self.trained=True; log("Modelo IA carregado",level='ai')
        except: log("Modelo IA será treinado em breve",level='ai')

    def _save(self):
        try:
            with open(MODEL_FILE,'wb') as f: pickle.dump(self.model,f)
        except Exception as e: log(f"Erro ao salvar modelo: {e}",level='error')

    def extract_features(self,df):
        try:
            if len(df)<60: return None
            price=df["c"].iloc[-1]
            e20=calc_ema(df["c"],20).iloc[-1]; e50=calc_ema(df["c"],50).iloc[-1]
            e200=calc_ema(df["c"],200).iloc[-1] if len(df)>=200 else e50
            rsi_v=calc_rsi(df).iloc[-1]; atr_v=calc_atr(df).iloc[-1]
            ml,ms,mh=calc_macd(df)
            bb_u,bb_m,bb_l=calc_bollinger(df)
            bb_pos=(price-bb_l.iloc[-1])/(bb_u.iloc[-1]-bb_l.iloc[-1]+1e-10)
            ichi_s,ichi_str=ichimoku_signal(df); vwap_s=vwap_signal(df)
            fib_s,_=fibonacci_signal(df); ms_d=market_structure(df)
            rev=reversal_candle(df); whl=whale_activity(df)
            vol_r=df["v"].iloc[-1]/(df["v"].iloc[-20:-1].mean()+1e-10)
            mom5=(price-df["c"].iloc[-5])/(df["c"].iloc[-5]+1e-10) if len(df)>=5 else 0
            mom10=(price-df["c"].iloc[-10])/(df["c"].iloc[-10]+1e-10) if len(df)>=10 else 0
            d20=(price-e20)/(price+1e-10); d50=(price-e50)/(price+1e-10); d200=(price-e200)/(price+1e-10)
            volatility=atr_v/price if price>0 else 0
            ms_bull=1 if ms_d["trend"]=="bullish" else -1 if ms_d["trend"]=="bearish" else 0
            feats=[d20,d50,d200,rsi_v/100,
                   ml.iloc[-1]/(price+1e-10),ms.iloc[-1]/(price+1e-10),mh.iloc[-1]/(price+1e-10),
                   bb_pos,ichi_s/4,ichi_str/4,vwap_s,fib_s,ms_bull,ms_d["strength"]/100,
                   1 if rev["detected"] and rev["type"]=="bullish" else 0,
                   1 if rev["detected"] and rev["type"]=="bearish" else 0,
                   1 if whl["detected"] else 0,vol_r/5,mom5,mom10,volatility*100,
                   1 if price>e20 else 0,1 if price>e50 else 0,1 if price>e200 else 0]
            return [float(x) if not(math.isnan(x) or math.isinf(x)) else 0.0 for x in feats]
        except Exception as e:
            log(f"Erro features: {e}",level='error'); return None

    def train(self,force=False):
        try:
            with get_db() as conn:
                rows=conn.execute("SELECT features,outcome FROM ai_training_data WHERE outcome IS NOT NULL ORDER BY id DESC LIMIT 2000").fetchall()
            if len(rows)<30 and not force:
                log(f"IA: {len(rows)} amostras — aguardando mais dados (mín 30)",level='ai'); return False
            X=[json.loads(r["features"]) for r in rows]; y=[r["outcome"] for r in rows]
            self.model=Pipeline([('sc',StandardScaler()),('clf',GradientBoostingClassifier(n_estimators=100,max_depth=4,learning_rate=0.1,random_state=42))])
            self.model.fit(X,y); self.trained=True; self.training_count=len(rows); self._save()
            log(f"🧠 IA treinada com {len(rows)} amostras!",level='ai'); return True
        except Exception as e:
            log(f"Erro treino IA: {e}",level='error'); return False

    def predict(self,features):
        if not self.trained or not self.model: return 50.0,"uncertain"
        try:
            proba=self.model.predict_proba([features])[0]; pred=self.model.predict([features])[0]
            return max(proba)*100,("bull" if pred==1 else "bear")
        except: return 50.0,"uncertain"

ai_engine=AIEngine()

# ==================== INSTITUTIONAL MODULES ====================
def detect_market_regime(df):
    """Detecta o regime de mercado: TRENDING, VOLATILE ou RANGING"""
    if len(df) < 50: return "RANGING"
    adx = calc_adx(df).iloc[-1]
    atr_pct = (calc_atr(df).iloc[-1] / df["c"].iloc[-1]) * 100
    
    if adx > 25: return "TRENDING"
    if atr_pct > 1.5: return "VOLATILE"
    return "RANGING"

def check_order_book_liquidity(symbol, side, quantity):
    """Verifica a profundidade do livro de ordens para evitar slippage excessivo"""
    try:
        depth = safe_req(client.futures_order_book, symbol=symbol, limit=20)
        bids = depth['bids'] # [price, qty]
        asks = depth['asks']
        
        target_qty = quantity * 2 # Queremos liquidez para 2x o nosso tamanho
        accumulated = 0
        orders = asks if side == "UP" else bids
        
        for p, q in orders:
            accumulated += float(q)
            if accumulated >= target_qty:
                slippage = abs(float(p) - float(orders[0][0])) / float(orders[0][0])
                if slippage > 0.002: # Máximo 0.2% de slippage no book
                    log(f"🚫 Slippage excessivo para {symbol}: {slippage:.4%}", level='reject')
                    return False
                return True
        return False
    except:
        return True # Se falhar a API, assume OK para não travar

# ==================== STRATEGY v4.2 ====================
def compute_score_v4(df, symbol=None):
    if len(df)<60: return {"score":0,"direction":"SIDE","signals":{}}
    price=df["c"].iloc[-1]
    e20=calc_ema(df["c"],20).iloc[-1]; e50=calc_ema(df["c"],50).iloc[-1]
    e200=calc_ema(df["c"],200).iloc[-1] if len(df)>=200 else e50
    rsi_v=calc_rsi(df).iloc[-1]; atr_v=calc_atr(df).iloc[-1]
    adx_v=calc_adx(df).iloc[-1] if len(df)>=30 else 0
    ml,ms,mh=calc_macd(df); macd_h=mh.iloc[-1]
    bb_u,bb_m,bb_l=calc_bollinger(df)
    ichi_s,ichi_str=ichimoku_signal(df); vwap_s=vwap_signal(df)
    fib_s,fib_lv=fibonacci_signal(df); ms_d=market_structure(df)
    rev=reversal_candle(df); whl=whale_activity(df)
    levels=support_resistance(df)
    near_sr=any(abs(l["price"]-price)<atr_v*1.5 for l in levels[:3])
    vol_r=df["v"].iloc[-1]/(df["v"].iloc[-20:-1].mean()+1e-10)
    buy=0; sell=0; bs=0; ss=0; sigs={}

    # [INSTITUCIONAL] Detecção de Regime
    regime = detect_market_regime(df)
    if regime == "RANGING":
        return {"score":0,"direction":"SIDE","signals":{"regime":"lateral_evitado"}}
    
    # [MODO SNIPER] Triplo Filtro de Tendência (5m, 15m, 1h)
    if symbol:
        df_15m = get_candles(symbol, "15m", limit=50)
        df_1h = get_candles(symbol, "1h", limit=50)
        if not df_15m.empty and not df_1h.empty:
            ema50_15m = calc_ema(df_15m["c"], 50).iloc[-1]
            ema50_1h = calc_ema(df_1h["c"], 50).iloc[-1]
            
            # Só compra se 15m e 1h estiverem em alta
            if price > ema50_15m and price > ema50_1h: buy += 40; sigs["mtf"] = "bull_aligned"
            # Só vende se 15m e 1h estiverem em baixa
            elif price < ema50_15m and price < ema50_1h: sell += 40; sigs["mtf"] = "bear_aligned"
            else: return {"score":0,"direction":"SIDE","signals":{"mtf":"desalinhado"}}
            
            # [INSTITUCIONAL] Se for VOLATILE, exige confirmação extra
            if regime == "VOLATILE" and adx_v < 30:
                return {"score":0,"direction":"SIDE","signals":{"regime":"volatilidade_sem_forca"}}

    # Filtro de Tendência EMA 200 (Filtro Mestre 5m)
    if price > e200:
        buy += 25; bs += 1; sigs["trend"] = "bull_200"
    elif price < e200:
        sell += 25; ss += 1; sigs["trend"] = "bear_200"

    # Alinhamento de Médias Curtas
    if price > e20 > e50: buy += 15; bs += 1; sigs["ema_align"] = "bull"
    elif price < e20 < e50: sell += 15; ss += 1; sigs["ema_align"] = "bear"
    
    # RSI com Zonas de Exaustão (Evitar comprar no topo ou vender no fundo)
    if 45 <= rsi_v <= 65: buy += 15; bs += 1; sigs["rsi"] = f"bull({rsi_v:.0f})"
    elif rsi_v > 75: buy -= 20; sigs["rsi"] = "sobrecomprado" # Penaliza compra em topo
    
    if 35 <= rsi_v <= 55: sell += 15; ss += 1; sigs["rsi"] = f"bear({rsi_v:.0f})"
    elif rsi_v < 25: sell -= 20; sigs["rsi"] = "sobrevendido" # Penaliza venda em fundo
    
    if macd_h>0 and ml.iloc[-1]>ms.iloc[-1]: buy+=12;bs+=1;sigs["macd"]="bull"
    elif macd_h<0 and ml.iloc[-1]<ms.iloc[-1]: sell+=12;ss+=1;sigs["macd"]="bear"
    
    if ichi_s==1: buy+=20;bs+=1;sigs["ichimoku"]=f"bull(f{ichi_str})"
    elif ichi_s==-1: sell+=20;ss+=1;sigs["ichimoku"]=f"bear(f{ichi_str})"
    
    if vwap_s==1: buy+=10;bs+=1;sigs["vwap"]="acima"
    elif vwap_s==-1: sell+=10;ss+=1;sigs["vwap"]="abaixo"
    
    if fib_s==1: buy+=15;bs+=1;sigs["fib"]=f"suporte {fib_lv['ratio'] if fib_lv else ''}"
    elif fib_s==-1: sell+=15;ss+=1;sigs["fib"]=f"resistência {fib_lv['ratio'] if fib_lv else ''}"
    
    if ms_d["trend"]=="bullish" and ms_d["strength"]>55: buy+=15;bs+=1;sigs["struct"]=f"bull{ms_d['strength']:.0f}%"
    elif ms_d["trend"]=="bearish" and ms_d["strength"]>55: sell+=15;ss+=1;sigs["struct"]=f"bear{ms_d['strength']:.0f}%"
    
    if rev["detected"]:
        if rev["type"]=="bullish": buy+=12;bs+=1;sigs["candle"]="rev_bull"
        elif rev["type"]=="bearish": sell+=12;ss+=1;sigs["candle"]="rev_bear"
    
    if whl["detected"]:
        if price>e50: buy+=10;sigs["whale"]=f"{whl['magnitude']:.1f}x"
        else: sell+=10;sigs["whale"]=f"{whl['magnitude']:.1f}x"
    
    if near_sr:
        if price>e50: buy+=10;sigs["sr"]="suporte"
        else: sell+=10;sigs["sr"]="resistência"
    
    if vol_r>1.3:
        if price>e50: buy+=10;bs+=1;sigs["vol"]=f"{vol_r:.1f}x"
        else: sell+=10;ss+=1;sigs["vol"]=f"{vol_r:.1f}x"
    
    if price>e50 and price<bb_u.iloc[-1]: buy+=8;sigs["bb"]="bull"
    elif price<e50 and price>bb_l.iloc[-1]: sell+=8;sigs["bb"]="bear"

    # Filtro de Volume Mínimo para Entrada
    if vol_r < 1.1:
        buy -= 10; sell -= 10; sigs["vol_low"] = "baixo_volume"

    if buy>sell and bs>=3: return {"score":buy,"direction":"UP","buy_sig":bs,"sell_sig":ss,"signals":sigs}
    if sell>buy and ss>=3: return {"score":sell,"direction":"DOWN","buy_sig":bs,"sell_sig":ss,"signals":sigs}
    return {"score":0,"direction":"SIDE","buy_sig":bs,"sell_sig":ss,"signals":sigs}

def hybrid_entry_signal(df):
    if len(df)<60: return None
    sd = compute_score_v4(df)
    if sd["direction"]=="SIDE": return None
    
    min_score = 75
    min_signals = 4
    
    final_score = sd["score"]
    buy_sig = sd.get("buy_sig", 0)
    sell_sig = sd.get("sell_sig", 0)
    signals_ok = (buy_sig >= min_signals if sd["direction"]=="UP" else sell_sig >= min_signals)

    if final_score < min_score or not signals_ok:
        log(f"❌ Score insuficiente: {final_score} / sinais: {buy_sig}B {sell_sig}S", level='debug')
        return None

    return {"signal":sd["direction"],"score":final_score,"justification":f"Score:{final_score} Sinais:{buy_sig}B/{sell_sig}S","signals":sd.get("signals",{})}

# ==================== RISK MANAGER ====================
class RiskManagerV4:
    def __init__(self):
        self.peak_balance=0; self.initial_balance=0
        self.win_rate_est = 0.55 # Estimativa inicial institucional

    def update_peak(self,bal):
        if bal>self.peak_balance: self.peak_balance=bal

    def get_risk(self,bal,ai_conf=55.0):
        """Implementação do Critério de Kelly para Position Sizing Institucional"""
        if bal<=0: return RISK_MIN
        
        # b = Profit / Loss (RR Ratio)
        b = RR 
        p = ai_conf / 100.0 if ai_conf > 50 else self.win_rate_est
        
        # Kelly % = (bp - q) / b  onde q = 1-p
        q = 1 - p
        kelly_f = (b * p - q) / b if b > 0 else 0
        
        # Usamos "Fractional Kelly" (20% do Kelly) para ser institucionalmente seguro
        safe_kelly = max(RISK_MIN, min(RISK_MAX, kelly_f * 0.20))
        
        # Drawdown Protection
        dd=(self.peak_balance-bal)/self.peak_balance if self.peak_balance>0 else 0
        if dd>0.10: safe_kelly *= 0.5 # Reduz risco pela metade se cair 10%
        
        # Ajuste para banca pequena: garantir que o risco permite o notional mínimo
        if bal < 100:
            return max(0.03, safe_kelly) 
        return round(safe_kelly, 4)

    def update_win_rate(self, history):
        if not history: return
        wins = len([t for t in history if t['pnl'] > 0])
        self.win_rate_est = wins / len(history) if len(history) > 0 else 0.55

    def calc_size(self,symbol,bal,entry,stop,risk_pct):
        if stop==entry or bal<=0: return 0
        # Corrigido: O risco deve ser sobre o capital total, não dividido pela alavancagem no cálculo do tamanho.
        # A alavancagem permite operar volumes maiores, mas o risco define quanto perdemos se o stop for atingido.
        risk_amount = bal * risk_pct
        dist = abs(entry - stop)
        if dist == 0: return 0
        qty = risk_amount / dist
        # Garantir que não excedemos o poder de compra (bal * alavancagem)
        max_qty = (bal * LEVERAGE * 0.95) / entry
        return adj_qty(symbol, min(qty, max_qty))

risk_manager=RiskManagerV4()

# ==================== BOT STATE ====================
lock=threading.Lock(); bot_on=False; positions={}; daily_loss=0.0; start_balance=0.0
scanner_data={"candidates":[],"last_update":0}

def sync_positions():
    global positions
    try:
        data=safe_req(client.futures_position_information)
        with lock:
            positions.clear()
            for p in data:
                amt=float(p["positionAmt"])
                if amt!=0:
                    positions[p["symbol"]]={"side":"UP" if amt>0 else "DOWN","entry":float(p["entryPrice"]),"qty":abs(amt),"entry_time":datetime.now().isoformat(),"partial_tp_done":0,"pyramid_count":0}
    except Exception as e: log(f"Erro sync: {e}",level='error')

# ==================== EXECUTION v4.2 - SEM DEADLOCK ====================


def execute_trade(symbol, side, score_data, ai_conf):
    global positions, daily_loss
    log(f"🔄 Tentando executar: {symbol} {side} | IA:{ai_conf:.0f}%", level='trade')
    
    if symbol in positions:
        log(f"⚠️ {symbol} já em posição", level='warning')
        return
    
    try:
        bal = get_balance()
        if bal <= 0:
            log(f"❌ Saldo zero", level='reject')
            return
        
        price = get_price(symbol)
        if not price:
            log(f"❌ Sem preço para {symbol}", level='reject')
            return
        
        df = get_candles(symbol, "5m")
        if df.empty:
            log(f"❌ Sem candles para {symbol}", level='reject')
            return
        
        atr_v = calc_atr(df).iloc[-1] if not calc_atr(df).empty else price * 0.01
        dynamic_risk = risk_manager.get_risk(bal, ai_conf)
        
        # Gerenciamento de Risco Profissional: SL curto, TP longo
        risk_dist = max(atr_v * 1.2, price * 0.004) 
        reward_dist = risk_dist * RR 

        if side == "UP":
            sl = adj_price(symbol, price - risk_dist)
            tp = adj_price(symbol, price + reward_dist)
            tp_partial = adj_price(symbol, price + (risk_dist * 1.1))
            os_ = "BUY"
            es_ = "SELL"
        else:
            sl = adj_price(symbol, price + risk_dist)
            tp = adj_price(symbol, price - reward_dist)
            tp_partial = adj_price(symbol, price - (risk_dist * 1.1))
            os_ = "SELL"
            es_ = "BUY"
        
        qty = risk_manager.calc_size(symbol, bal, price, sl, dynamic_risk)
        if qty <= 0:
            log(f"❌ Qty zero {symbol}", level='reject')
            return
        
        min_not = symbol_filters.get(symbol, {}).get("min_notional", 5)
        if qty * price < min_not:
            qty = adj_qty(symbol, (min_not * 1.05) / price)
            if qty * price > bal * LEVERAGE:
                log(f"❌ Saldo insuficiente para notional mínimo: {symbol}", level='reject')
                return

        # EXECUÇÃO NA BINANCE
        safe_req(client.futures_change_leverage, symbol=symbol, leverage=LEVERAGE)
        try:
            safe_req(client.futures_change_margin_type, symbol=symbol, marginType="ISOLATED")
        except: pass
            
        # 1. Ordem Principal
        order = safe_req(client.futures_create_order, symbol=symbol, side=os_, type="MARKET", quantity=qty)
        entry_price = float(order.get('avgPrice', 0)) or float(order.get('price', 0)) or price
        
        # 2. Ordem de STOP LOSS REAL na Binance
        try:
            safe_req(client.futures_create_order, 
                     symbol=symbol, side=es_, type="STOP_MARKET", 
                     stopPrice=sl, quantity=qty, reduceOnly=True)
            log(f"🛡️ Stop Loss Real definido em {sl}", level='success')
        except Exception as e:
            log(f"⚠️ Falha ao definir SL real {symbol}: {e}", level='error')

        # 3. Ordem de TAKE PROFIT REAL na Binance (LIMIT)
        try:
            safe_req(client.futures_create_order, 
                     symbol=symbol, side=es_, type="LIMIT", 
                     price=tp, quantity=qty, timeInForce="GTC", reduceOnly=True)
            log(f"💰 Take Profit Real definido em {tp}", level='success')
        except Exception as e:
            log(f"⚠️ Falha ao definir TP real {symbol}: {e}", level='error')

        pd_ = {
            "side": side, "entry": entry_price, "qty": qty,
            "entry_time": datetime.now().isoformat(),
            "stop_loss": sl, "take_profit": tp, "tp_partial": tp_partial,
            "partial_tp_done": 0, "pyramid_count": 0, "trailing_activated": 0,
            "highest_price": entry_price, "lowest_price": entry_price,
            "risk_used": dynamic_risk, "ai_confidence": ai_conf,
            "score": score_data.get("score", 0)
        }
        
        with lock:
            positions[symbol] = pd_
        save_position(symbol, pd_)
        log(f"✅ TRADE ABERTO: {symbol} {side} | Qty:{qty} | Entrada:${entry_price:.4f} | SL:${sl:.4f} | TP:${tp:.4f}", level='trade')
        
    except Exception as e:
        log(f"❌ ERRO execução {symbol}: {e}", level='error')

        
    except Exception as e:
        log(f"❌ ERRO execução {symbol}: {e}", level='error')

        
    except BinanceAPIException as e:
        log(f"❌ ERRO BINANCE {symbol}: {e.message} (código: {e.code})", level='error')
    except Exception as e:
        log(f"❌ ERRO execução {symbol}: {type(e).__name__}: {e}", level='error')



def manage_positions():
    global positions, daily_loss
    to_remove = []
    with lock:
        positions_copy = dict(positions)
    
    try:
        binance_positions = safe_req(client.futures_position_information)
        active_symbols = {p['symbol']: abs(float(p['positionAmt'])) for p in binance_positions if float(p['positionAmt']) != 0}
    except:
        active_symbols = {s: pos['qty'] for s, pos in positions_copy.items()}

    for symbol, pos in positions_copy.items():
        if symbol not in active_symbols:
            # Se a posição sumiu da Binance, ela foi stopada ou fechada manualmente
            log(f"🧹 Posição {symbol} encerrada na exchange. Sincronizando...", level='info')
            # Tentar recuperar o PnL do último trade para o histórico
            try:
                trades = safe_req(client.futures_account_trades, symbol=symbol, limit=5)
                last_trade = trades[-1]
                real_pnl = float(last_trade.get('realizedPnl', 0))
                # Se o PnL for negativo, adiciona à perda diária
                if real_pnl < 0:
                    daily_loss += abs(real_pnl)
                
                save_trade({
                    "symbol": symbol, "side": pos['side'], "entry_price": pos['entry'], 
                    "exit_price": float(last_trade.get('price', 0)),
                    "quantity": pos['qty'], "pnl": real_pnl, 
                    "pnl_pct": (real_pnl / (pos['entry'] * pos['qty'] / LEVERAGE)) * 100 if pos['entry'] > 0 else 0,
                    "entry_time": pos["entry_time"], "exit_time": datetime.now().isoformat(),
                    "reason": "exchange_event", "risk_used": pos.get("risk_used", 0.03),
                    "ai_confidence": pos.get("ai_confidence", 0), "score": pos.get("score", 0)
                })
            except: pass
            to_remove.append(symbol)
            delete_position(symbol)
            continue

        try:
            price = get_price(symbol)
            if not price: continue
            
            entry = pos["entry"]; qty = pos["qty"]; side = pos["side"]
            sl = pos.get("stop_loss", 0); tp = pos.get("take_profit", 0)
            
            # Atualizar máximos/mínimos para Trailing
            if side == "UP":
                if price > pos.get("highest_price", entry):
                    with lock: 
                        if symbol in positions: positions[symbol]["highest_price"] = price
            else:
                if price < pos.get("lowest_price", entry):
                    with lock:
                        if symbol in positions: positions[symbol]["lowest_price"] = price
            
            pnl_pct = ((price - entry) / entry * 100) if side == "UP" else ((entry - price) / entry * 100)
            
            # Trailing Stop Dinâmico (Proteção de Lucro)
            # Se lucro > 0.5%, ativa trailing de 0.3% do topo
            close = False
            reason = ""
            
            if pnl_pct > 0.5:
                if not pos.get("trailing_activated"):
                    with lock:
                        if symbol in positions: positions[symbol]["trailing_activated"] = 1
                
                trail_price = pos.get("highest_price", entry) * 0.997 if side == "UP" else pos.get("lowest_price", entry) * 1.003
                if (side == "UP" and price < trail_price) or (side == "DOWN" and price > trail_price):
                    close = True; reason = "trailing_stop"
            
            # TP / SL Virtual (Backup do Real)
            if not close:
                if (side == "UP" and price >= tp) or (side == "DOWN" and price <= tp):
                    close = True; reason = "take_profit"
                elif (side == "UP" and price <= sl) or (side == "DOWN" and price >= sl):
                    close = True; reason = "stop_loss"

            
            if close:
                cs_ = "SELL" if side == "UP" else "BUY"
                log(f"🚨 FECHAMENTO FORÇADO PELO BOT: {symbol} por {reason} | Preço: {price}", level='risk')
                try:
                    # Tenta cancelar ordens existentes primeiro
                    try: safe_req(client.futures_cancel_all_open_orders, symbol=symbol)
                    except: pass
                    
                    # Ordem de mercado para fechar tudo
                    order = safe_req(client.futures_create_order, symbol=symbol, side=cs_, type="MARKET", quantity=qty, reduceOnly=True)
                    final_price = float(order.get('avgPrice', 0)) or float(order.get('price', 0)) or price
                    real_pnl = (final_price - entry) * qty if side == "UP" else (entry - final_price) * qty
                    
                    log(f"🏁 Posição {symbol} encerrada com sucesso via Bot Monitor. PnL: ${real_pnl:.2f}", level='success')
                    
                    if real_pnl < 0:
                        daily_loss += abs(real_pnl)
                    
                    save_trade({
                        "symbol": symbol, "side": side, "entry_price": entry, "exit_price": final_price,
                        "quantity": qty, "pnl": real_pnl, "pnl_pct": pnl_pct,
                        "entry_time": pos["entry_time"], "exit_time": datetime.now().isoformat(),
                        "reason": f"bot_{reason}", "risk_used": pos.get("risk_used", 0.03),
                        "ai_confidence": pos.get("ai_confidence", 0), "score": pos.get("score", 0)
                    })
                    to_remove.append(symbol)
                    delete_position(symbol)
                except Exception as e:
                    log(f"❌ FALHA CRÍTICA ao fechar {symbol} via Bot Monitor: {e}. Tentando novamente em 1s...", level='error')

                except Exception as e:
                    log(f"❌ Erro ao fechar {symbol}: {e}", level='error')
                    
        except Exception as e:
            log(f"Erro manage {symbol}: {e}", level='error')
            
    if to_remove:
        with lock:
            for s in to_remove: positions.pop(s, None)



def find_candidates():
    global scanner_data
    try:
        data = safe_req(client.futures_ticker)
        all_scores = []
        candidates = []
        
        for x in data:
            symbol = x["symbol"]
            if not symbol.endswith("USDT") or symbol not in symbol_filters:
                continue
            price = float(x["lastPrice"])
            volume = float(x["quoteVolume"])
            if price > MAX_PRICE or volume < 3_000_000:
                continue
            df = get_candles(symbol, "5m", limit=100)
            if df.empty or len(df) < 60:
                continue
            sd = compute_score_v4(df)
            atr_v = calc_atr(df).iloc[-1] if not calc_atr(df).empty else 0
            vs = min(volume / 50_000_000 * 10, 30)
            vola = (atr_v / price) * 1000 if price > 0 else 0
            total = sd["score"] + vs + min(vola, 30)
            all_scores.append({
                "symbol": symbol, "score": round(total, 1), "price": round(price, 4),
                "trend": sd["direction"], "volume": round(volume / 1_000_000, 1),
                "volatility": round(vola, 2), "signals": sd.get("signals", {})
            })
            if sd["direction"] != "SIDE":
                candidates.append((symbol, total))
        
        all_scores.sort(key=lambda x: x["score"], reverse=True)
        scanner_data["candidates"] = all_scores[:50]
        scanner_data["last_update"] = time.time()
        candidates.sort(key=lambda x: x[1], reverse=True)
        if candidates:
            log(f"Top: {[c[0] for c in candidates[:3]]}", level='debug')
        return [c[0] for c in candidates[:10]]
    except Exception as e:
        log(f"Erro scanner: {e}", level='error')
        return []

# ==================== BOT LOOP v4.2 - SEM DEADLOCK ====================
def bot_loop():
    global bot_on, daily_loss, positions, start_balance
    log("🚀 Nostradamus v4.2 iniciado! Ichimoku+VWAP+Fibonacci+IA", level='success')
    sync_positions()
    start_balance = get_balance()
    save_state("start_balance", start_balance)
    risk_manager.initial_balance = start_balance
    risk_manager.peak_balance = start_balance
    last_sync = time.time()
    last_train = time.time()
    
    while bot_on:
        try:
            bal = get_balance()
            if bal <= 0:
                log("Saldo zerado!", level='risk')
                bot_on = False
                break
            
            risk_manager.update_peak(bal)
            
            if daily_loss > start_balance * DAILY_LOSS_LIMIT:
                log(f"Stop diário! ${daily_loss:.2f}", level='risk')
                bot_on = False
                break
            
            if time.time() - last_sync > 300:
                sync_positions()
                last_sync = time.time()
            
            if time.time() - last_train > 3600:
                threading.Thread(target=ai_engine.train, daemon=True).start()
                last_train = time.time()
            
            # ENCONTRA SINAL DENTRO DO LOCK
            sym_to_trade = None
            side_to_trade = None
            score_to_trade = None
            ai_conf_to_trade = None
            
            with lock:
                if len(positions) < MAX_TRADES:
                    for sym in find_candidates():
                        if sym in positions:
                            continue
                        df = get_candles(sym, "5m")
                        if df.empty or len(df) < 60:
                            continue
                        signal = hybrid_entry_signal(df)
                        if not signal:
                            continue
                        
                        sd = {"score": signal["score"], "direction": signal["signal"], "signals": signal.get("signals", {})}
                        features = ai_engine.extract_features(df)
                        ai_conf = 50.0
                        ai_dir = "uncertain"
                        
                        if features and ai_engine.trained:
                            ai_conf, ai_dir = ai_engine.predict(features)
                            log(f"🧠 IA: {sym} → {ai_dir} ({ai_conf:.0f}%)", level='ai')
                            tech_dir = "bull" if sd["direction"] == "UP" else "bear"
                            if ai_dir != tech_dir and ai_conf > 65:
                                log(f"🧠 IA discorda: {sym}", level='ai')
                                continue
                        elif features:
                            ai_conf = 55.0
                        
                        if ai_conf < AI_MIN_CONFIDENCE and ai_engine.trained:
                            log(f"🧠 IA baixa: {sym} {ai_conf:.0f}%", level='ai')
                            continue
                        
                        log(f"💰 SINAL v4.2: {sym} {sd['direction']} | Score:{sd['score']} | IA:{ai_conf:.0f}%", level='trade')
                        
                        sym_to_trade = sym
                        side_to_trade = sd["direction"]
                        score_to_trade = sd
                        ai_conf_to_trade = ai_conf
                        break  # Sai do for e do lock
            
            # EXECUTA O TRADE FORA DO LOCK
            if sym_to_trade:
                # [INSTITUCIONAL] Verificação de Liquidez antes da execução
                if check_order_book_liquidity(sym_to_trade, side_to_trade, 1.0): # 1.0 é placeholder, o execute_trade calcula o real
                    execute_trade(sym_to_trade, side_to_trade, score_to_trade, ai_conf_to_trade)
                else:
                    log(f"🚫 Trade cancelado por falta de liquidez institucional: {sym_to_trade}", level='reject')
                
            manage_positions()
            save_state("daily_loss", daily_loss)
            
        except Exception as e:
            log(f"Erro loop: {e}", level='error')
        
        time.sleep(INTERVAL)

# ==================== AUTH ====================
JWT_SECRET = os.getenv("JWT_SECRET", "nostradamus-v4")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

class Auth:
    @staticmethod
    def verify_password(pw):
        if not ADMIN_PASSWORD_HASH:
            return False
        try:
            return bcrypt.checkpw(pw.encode(), ADMIN_PASSWORD_HASH.encode())
        except:
            return hashlib.sha256(pw.encode()).hexdigest() == ADMIN_PASSWORD_HASH

    @staticmethod
    def create_token():
        return jwt.encode({"exp": datetime.utcnow() + timedelta(hours=24), "iat": datetime.utcnow(), "role": "admin"}, JWT_SECRET, algorithm="HS256")

    @staticmethod
    def verify_token(token):
        try:
            jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            return True
        except:
            return False

# ==================== STARTUP ====================
bot_on = load_state("bot_on", False)
positions = load_positions()
daily_loss = load_state("daily_loss", 0.0)
start_balance = load_state("start_balance", 0.0)


def fast_monitor_loop():
    log("🚀 Monitoramento Ultra-Rápido de TP/SL iniciado (1s)", level='info')
    while True:
        try:
            if bot_on:
                manage_positions()
        except Exception as e:
            log(f"Erro no monitoramento rápido: {e}", level='error')
        time.sleep(1) # Verifica a cada 1 segundo

def scanner_loop():
    log("Scanner background iniciado", level='info')
    while True:
        try:
            find_candidates()
        except Exception as e:
            log(f"Erro scanner: {e}", level='error')
        time.sleep(60)

@app.on_event("startup")
async def on_startup():
    log("Nostradamus v4.2.1 [MANUS FIX] — Sistema online e pronto para operar", level='success')
    threading.Thread(target=ai_engine.train, daemon=True).start()
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=fast_monitor_loop, daemon=True).start()
    if bot_on:
        threading.Thread(target=bot_loop, daemon=True).start()
    else:
        log("Bot aguardando comando", level='info')

@app.on_event("shutdown")
async def on_shutdown():
    global bot_on
    bot_on = False
    save_state("bot_on", False)

# ==================== ROUTES ====================
@app.get("/")
async def root():
    p = os.path.join(static_dir, "index.html")
    return FileResponse(p) if os.path.exists(p) else {"version": "4.2.0"}

@app.get("/login")
async def login_page():
    p = os.path.join(static_dir, "login.html")
    return FileResponse(p) if os.path.exists(p) else HTMLResponse("<h1>404</h1>", 404)

@app.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    pw = body.get("password", "")
    if Auth.verify_password(pw):
        return {"token": Auth.create_token(), "expires_in": 86400}
    raise HTTPException(401, "Senha inválida")

@app.post("/auth/verify")
async def verify(request: Request):
    body = await request.json()
    return {"valid": Auth.verify_token(body.get("token", ""))}

@app.get("/api/positions")

async def get_active_positions():
    pos_list = []
    try:
        # Consultar posições reais na Binance
        binance_data = safe_req(client.futures_position_information)
        real_positions = {p['symbol']: p for p in binance_data if float(p['positionAmt']) != 0}
        
        # Consultar ordens abertas para pegar TP/SL reais
        open_orders = safe_req(client.futures_get_open_orders)
        orders_by_symbol = {}
        for o in open_orders:
            s = o['symbol']
            if s not in orders_by_symbol: orders_by_symbol[s] = []
            orders_by_symbol[s].append(o)

        with lock:
            for sym, p_real in real_positions.items():
                amt = float(p_real['positionAmt'])
                side = "UP" if amt > 0 else "DOWN"
                entry = float(p_real['entryPrice'])
                curr = float(p_real['markPrice'])
                qty = abs(amt)
                pnl = float(p_real['unRealizedProfit'])
                roe = (pnl / (max(0.01, entry * qty / LEVERAGE))) * 100 if entry > 0 else 0
                
                # Tentar pegar TP/SL das ordens abertas ou do banco de dados
                sl = 0
                tp = 0
                if sym in orders_by_symbol:
                    for o in orders_by_symbol[sym]:
                        if o['type'] == 'STOP_MARKET': sl = float(o['stopPrice'])
                        if o['type'] == 'LIMIT': tp = float(o['price'])
                
                # Fallback para o banco de dados se não achar ordens
                if sl == 0 and sym in positions: sl = positions[sym].get('stop_loss', 0)
                if tp == 0 and sym in positions: tp = positions[sym].get('take_profit', 0)

                pos_list.append({
                    "symbol": sym, "side": side, "entry": round(entry, 4), "qty": round(qty, 4),
                    "current_price": round(curr, 4), "pnl": round(pnl, 2), "roe": round(roe, 2),
                    "risk_used": positions.get(sym, {}).get("risk_used", 0.03) * 100,
                    "ai_confidence": positions.get(sym, {}).get("ai_confidence", 0),
                    "score": positions.get(sym, {}).get("score", 0),
                    "take_profit": round(tp, 4), "stop_loss": round(sl, 4),
                    "entry_time": positions.get(sym, {}).get("entry_time", datetime.now().isoformat())
                })
    except Exception as e:
        log(f"Erro ao buscar posições reais: {e}", level='error')
    return {"positions": pos_list}


@app.get("/api/status")
async def status():
    bal = get_balance()
    # Forçar atualização do win_rate no RiskManager
    with get_db() as conn:
        history = [dict(r) for r in conn.execute("SELECT pnl FROM trade_history").fetchall()]
        risk_manager.update_win_rate(history)
        
        t = conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]
        w = conn.execute("SELECT COUNT(*) FROM trade_history WHERE pnl>0").fetchone()[0]
        tpnl = conn.execute("SELECT SUM(pnl) FROM trade_history").fetchone()[0] or 0
        best = conn.execute("SELECT MAX(pnl) FROM trade_history").fetchone()[0] or 0
        worst = conn.execute("SELECT MIN(pnl) FROM trade_history").fetchone()[0] or 0
        tg = conn.execute("SELECT SUM(pnl) FROM trade_history WHERE pnl>0").fetchone()[0] or 0
        tl = conn.execute("SELECT SUM(pnl) FROM trade_history WHERE pnl<0").fetchone()[0] or 0
        ai_s = conn.execute("SELECT COUNT(*) FROM ai_training_data").fetchone()[0]
    
    pos_list = (await get_active_positions())["positions"]
    wr = (w / t * 100) if t > 0 else 0
    pf = tg / abs(tl) if tl != 0 else 0
    
    return {
        "running": bot_on, "testnet": BINANCE_TESTNET or BINANCE_DEMO, "demo": BINANCE_DEMO, "positions": pos_list,
        "daily_loss": round(daily_loss, 2), "daily_loss_limit": DAILY_LOSS_LIMIT,
        "daily_loss_percentage": round((daily_loss / bal) * 100, 2) if bal > 0 else 0,
        "current_balance": round(bal, 2), "start_balance": round(start_balance, 2),
        "total_pnl": round(tpnl, 2), # Usar o PnL real do histórico
        "ai": {"trained": ai_engine.trained, "training_samples": ai_s, "training_count": ai_engine.training_count},
        "config": {"leverage": LEVERAGE, "risk": RISK, "rr_ratio": RR, "max_trades": MAX_TRADES,
                   "daily_loss_limit": DAILY_LOSS_LIMIT * 100, "min_backtest_confidence": MIN_BACKTEST_CONFIDENCE,
                   "protect_capital": PROTECT_CAPITAL, "max_price": MAX_PRICE,
                   "ai_min_confidence": AI_MIN_CONFIDENCE, "partial_tp": PARTIAL_TP_ENABLED, "pyramid": PYRAMID_ENABLED},
        "metrics": {"trades": t, "wins": w, "losses": t - w, "win_rate": round(wr, 2),
                    "profit_factor": round(pf, 2), "total_pnl": round(tpnl, 2),
                    "best_trade": round(best, 2), "worst_trade": round(worst, 2)}
    }

@app.get("/api/scanner")
async def scanner():
    return {"candidates": scanner_data["candidates"], "last_update": scanner_data["last_update"]}

@app.get("/api/history")
async def history(limit: int = 100):
    with get_db() as conn:
        return {"trades": [dict(r) for r in conn.execute("SELECT * FROM trade_history ORDER BY exit_time DESC LIMIT ?", (limit,)).fetchall()]}

@app.get("/api/ai/status")
async def ai_status():
    with get_db() as conn:
        s = conn.execute("SELECT COUNT(*) FROM ai_training_data").fetchone()[0]
        o = conn.execute("SELECT COUNT(*) FROM ai_training_data WHERE outcome IS NOT NULL").fetchone()[0]
        ww = conn.execute("SELECT COUNT(*) FROM ai_training_data WHERE outcome=1").fetchone()[0]
    return {"trained": ai_engine.trained, "total_samples": s, "labeled_samples": o,
            "ai_accuracy": round(ww / o * 100, 1) if o > 0 else 0, "training_count": ai_engine.training_count}

@app.post("/api/ai/train")
async def force_train():
    result = ai_engine.train(force=True)
    return {"success": result, "trained": ai_engine.trained, "samples": ai_engine.training_count}

@app.post("/api/bot/start")
async def start():
    global bot_on
    if bot_on:
        return {"status": "already_running"}
    bot_on = True
    save_state("bot_on", True)
    threading.Thread(target=bot_loop, daemon=True).start()
    return {"status": "started"}

@app.post("/api/bot/stop")
async def stop():
    global bot_on
    bot_on = False
    save_state("bot_on", False)
    return {"status": "stopped"}

@app.get("/api/test_trade/{symbol}/{side}")
async def test_trade(symbol: str, side: str):
    if side.upper() not in ["UP", "DOWN"]:
        return {"status": "erro", "error": "Side deve ser UP ou DOWN"}
    try:
        df = get_candles(symbol.upper(), "5m")
        sd = compute_score_v4(df) if not df.empty else {"score": 100, "direction": side.upper(), "signals": {}}
        execute_trade(symbol.upper(), side.upper(), sd, 75.0)
        return {"status": "teste iniciado", "symbol": symbol.upper(), "side": side.upper(), "message": "Verifique os logs"}
    except Exception as e:
        return {"status": "erro", "error": str(e)}

@app.get("/api/balance")
async def balance():
    return {"balance": round(get_balance(), 2)}

@app.get("/api/metrics")
async def metrics():
    with get_db() as conn:
        t = conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]
        w = conn.execute("SELECT COUNT(*) FROM trade_history WHERE pnl>0").fetchone()[0]
        p = conn.execute("SELECT SUM(pnl) FROM trade_history").fetchone()[0] or 0
        g = conn.execute("SELECT SUM(pnl) FROM trade_history WHERE pnl>0").fetchone()[0] or 0
        l = conn.execute("SELECT SUM(pnl) FROM trade_history WHERE pnl<0").fetchone()[0] or 0
    return {"trades": t, "wins": w, "losses": t - w, "win_rate": round(w / t * 100, 2) if t else 0,
            "profit_factor": round(g / abs(l), 2) if l else 0, "total_pnl": round(p, 2)}

@app.get("/teste")
async def teste():
    return {"status": "ok", "version": "4.2.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
