
import os
import re

def apply_institutional_upgrade():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # 1. Adicionar Regime Detection e Multi-Timeframe Confluence
    institutional_logic = """
# ==================== INSTITUTIONAL LOGIC ====================
def detect_market_regime(df):
    if df.empty or len(df) < 50: return "UNKNOWN", 0.0
    
    # ADX para força da tendência
    adx = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14).adx().iloc[-1]
    # EMA para direção
    ema20 = ta.trend.ema_indicator(df['close'], window=20).iloc[-1]
    ema50 = ta.trend.ema_indicator(df['close'], window=50).iloc[-1]
    # ATR para volatilidade
    atr = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14).iloc[-1]
    
    price = df['close'].iloc[-1]
    
    if adx > 25:
        if price > ema20 > ema50: return "STRONG_TREND_UP", adx/100
        if price < ema20 < ema50: return "STRONG_TREND_DOWN", adx/100
        return "TRENDING", adx/100
    elif adx < 20:
        return "CHOP_RANGE", (25-adx)/25
    return "NEUTRAL", 0.5

def get_mtf_confluence(symbol):
    timeframes = ["1h", "4h", "1d"] # Simplificado para performance na VPS
    scores = []
    for tf in timeframes:
        df = get_candles(symbol, tf, limit=50)
        if df.empty: continue
        regime, conf = detect_market_regime(df)
        if regime == "STRONG_TREND_UP": scores.append(1)
        elif regime == "STRONG_TREND_DOWN": scores.append(-1)
        else: scores.append(0)
    
    total_score = sum(scores)
    if total_score >= 2: return "UP"
    if total_score <= -2: return "DOWN"
    return "NEUTRAL"

class DrawdownManager:
    def __init__(self):
        self.levels = [0.03, 0.05, 0.08, 0.12, 0.15]
        self.risk_multipliers = [0.5, 0.3, 0.1, 0.0, 0.0]
        
    def get_risk_multiplier(self, current_dd):
        for i, level in enumerate(self.levels):
            if current_dd >= level:
                return self.risk_multipliers[i]
        return 1.0

def should_operate_equity_filter():
    with get_db() as conn:
        history = conn.execute("SELECT pnl FROM trade_history ORDER BY id DESC LIMIT 20").fetchall()
        if len(history) < 5: return True
        pnls = [r[0] for r in history]
        # Se os últimos 3 trades foram loss, pausa
        if all(p < 0 for p in pnls[:3]): return False
        return True

drawdown_manager = DrawdownManager()
# =============================================================
"""
    # Inserir antes da lógica de trading
    if "# ==================== INSTITUTIONAL LOGIC ====================" not in content:
        content = content.replace("# ==================== TRADING LOGIC ====================", institutional_logic + "\n# ==================== TRADING LOGIC ====================")

    # 2. Aplicar filtros no bot_loop
    new_filters = """
            # FILTROS INSTITUCIONAIS
            if not should_operate_equity_filter():
                log("🛡️ Equity Curve Filter: Pausando operações por sequência de perdas.", level='warning')
                time.sleep(300)
                continue

            # Verificação de Drawdown
            current_bal = get_balance()
            peak = load_state("peak_balance", start_balance)
            if current_bal > peak:
                peak = current_bal
                save_state("peak_balance", peak)
            
            drawdown = (peak - current_bal) / peak if peak > 0 else 0
            risk_mult = drawdown_manager.get_risk_multiplier(drawdown)
            
            if risk_mult == 0:
                log(f"🚨 Drawdown Crítico ({drawdown:.1%}): Operações suspensas.", level='risk')
                time.sleep(3600)
                continue
"""
    content = content.replace("while bot_on:", "while bot_on:" + new_filters)

    # 3. Aplicar Confluência no find_candidates/hybrid_entry_signal
    # Modificar a lógica de entrada para exigir confluência MTF
    content = content.replace(
        "signal = hybrid_entry_signal(df)",
        """signal = hybrid_entry_signal(df)
                        if signal:
                            mtf_dir = get_mtf_confluence(sym)
                            if mtf_dir != signal["direction"]:
                                log(f"🚫 MTF Discorda: {sym} (Signal:{signal['direction']} | MTF:{mtf_dir})", level='reject')
                                signal = None"""
    )

    with open(path, 'w') as f:
        f.write(content)
    print("Upgrade Institucional aplicado.")

if __name__ == "__main__":
    apply_institutional_upgrade()
