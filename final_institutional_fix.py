
import os

def final_institutional_fix():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        lines = f.readlines()

    # Encontra o local ideal para inserir as funções (antes do bot_loop)
    insert_idx = -1
    for i, line in enumerate(lines):
        if "def bot_loop():" in line:
            insert_idx = i
            break
    
    if insert_idx != -1:
        institutional_functions = [
            "\n",
            "# ==================== INSTITUTIONAL LOGIC ====================\n",
            "def detect_market_regime(df):\n",
            "    if df.empty or len(df) < 50: return \"UNKNOWN\", 0.0\n",
            "    try:\n",
            "        adx = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14).adx().iloc[-1]\n",
            "        ema20 = ta.trend.ema_indicator(df['close'], window=20).iloc[-1]\n",
            "        ema50 = ta.trend.ema_indicator(df['close'], window=50).iloc[-1]\n",
            "        price = df['close'].iloc[-1]\n",
            "        if adx > 25:\n",
            "            if price > ema20 > ema50: return \"STRONG_TREND_UP\", adx/100\n",
            "            if price < ema20 < ema50: return \"STRONG_TREND_DOWN\", adx/100\n",
            "            return \"TRENDING\", adx/100\n",
            "        elif adx < 20: return \"CHOP_RANGE\", (25-adx)/25\n",
            "        return \"NEUTRAL\", 0.5\n",
            "    except: return \"NEUTRAL\", 0.5\n",
            "\n",
            "def get_mtf_confluence(symbol):\n",
            "    timeframes = [\"1h\", \"4h\", \"1d\"]\n",
            "    scores = []\n",
            "    for tf in timeframes:\n",
            "        try:\n",
            "            df = get_candles(symbol, tf, limit=50)\n",
            "            if df.empty: continue\n",
            "            regime, conf = detect_market_regime(df)\n",
            "            if regime == \"STRONG_TREND_UP\": scores.append(1)\n",
            "            elif regime == \"STRONG_TREND_DOWN\": scores.append(-1)\n",
            "            else: scores.append(0)\n",
            "        except: continue\n",
            "    total_score = sum(scores)\n",
            "    if total_score >= 2: return \"UP\"\n",
            "    if total_score <= -2: return \"DOWN\"\n",
            "    return \"NEUTRAL\"\n",
            "\n",
            "class DrawdownManager:\n",
            "    def __init__(self):\n",
            "        self.levels = [0.03, 0.05, 0.08, 0.12, 0.15]\n",
            "        self.risk_multipliers = [0.5, 0.3, 0.1, 0.0, 0.0]\n",
            "    def get_risk_multiplier(self, current_dd):\n",
            "        for i, level in enumerate(self.levels): \n",
            "            if current_dd >= level: return self.risk_multipliers[i]\n",
            "        return 1.0\n",
            "\n",
            "def should_operate_equity_filter():\n",
            "    try:\n",
            "        with get_db() as conn:\n",
            "            history = conn.execute(\"SELECT pnl FROM trade_history ORDER BY id DESC LIMIT 5\").fetchall()\n",
            "            if len(history) < 3: return True\n",
            "            pnls = [r[0] for r in history]\n",
            "            if all(p < 0 for p in pnls[:3]): return False\n",
            "            return True\n",
            "    except: return True\n",
            "\n",
            "drawdown_manager = DrawdownManager()\n",
            "# =============================================================\n",
            "\n"
        ]
        # Insere as funções antes do bot_loop
        for line in reversed(institutional_functions):
            lines.insert(insert_idx, line)

    with open(path, 'w') as f:
        f.writelines(lines)
    print("Funções institucionais inseridas com sucesso.")

if __name__ == "__main__":
    final_institutional_fix()
