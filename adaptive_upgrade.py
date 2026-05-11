
import os

def apply_upgrade():
    path = "/home/ubuntu/nostradamus-bot/main.py"
    with open(path, "r") as f:
        content = f.read()

    # 1. Inserir as novas classes e funções antes do RiskManager
    new_components = """
# ==================== ADAPTIVE SELECTIVITY & PROTECTION ====================
class AdaptiveSelector:
    @staticmethod
    def get_params(balance):
        if 30 <= balance < 50:
            return {
                "mode": "MICRO", "confirmations": 2, "adx_min": 18, "vol_mult": 0.8, "min_score": 35,
                "leverage": 3, "risk_per_trade": 0.003, "use_mtf": False, "use_advanced": False
            }
        elif 50 <= balance < 100:
            return {
                "mode": "ULTRA_SMALL", "confirmations": 2, "adx_min": 20, "vol_mult": 0.9, "min_score": 40,
                "leverage": 5, "risk_per_trade": 0.005, "use_mtf": False, "use_advanced": False
            }
        elif 100 <= balance < 500:
            return {
                "mode": "SMALL", "confirmations": 3, "adx_min": 22, "vol_mult": 1.0, "min_score": 45,
                "leverage": 10, "risk_per_trade": 0.01, "use_mtf": True, "use_advanced": False
            }
        elif 500 <= balance < 2000:
            return {
                "mode": "MEDIUM", "confirmations": 3, "adx_min": 24, "vol_mult": 1.1, "min_score": 55,
                "leverage": 15, "risk_per_trade": 0.02, "use_mtf": True, "use_advanced": True
            }
        else: # 2000+
            return {
                "mode": "LARGE", "confirmations": 4, "adx_min": 26, "vol_mult": 1.2, "min_score": 65,
                "leverage": 20, "risk_per_trade": 0.03, "use_mtf": True, "use_advanced": True
            }

class LossGuard:
    def __init__(self):
        self.consecutive_losses = 0
        self.pause_until = None
        self.min_balance = 25.0
        self.daily_loss_limit_pct = 0.05

    def register_trade(self, pnl):
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 3:
                self.pause_until = datetime.now() + timedelta(minutes=30)
                log(f"🛡️ 3 perdas seguidas! Pausando por 30 min.", level='risk')
        else:
            self.consecutive_losses = 0

    def can_trade(self, balance, daily_pnl, start_bal):
        if balance < self.min_balance:
            log(f"🚫 Saldo abaixo do mínimo ($25). Travando bot.", level='risk')
            return False
        if self.pause_until and datetime.now() < self.pause_until:
            log(f"⏳ Bot em pausa técnica pós-perdas.", level='info')
            return False
        if daily_pnl < -(start_bal * self.daily_loss_limit_pct):
            log(f"🚫 Limite de perda diária (5%) atingido.", level='risk')
            return False
        return True

loss_guard = LossGuard()

def get_signal_micro_mode(df, params):
    if len(df) < 60: return None
    price = df['c'].iloc[-1]
    e20 = calc_ema(df['c'], 20).iloc[-1]
    e50 = calc_ema(df['c'], 50).iloc[-1]
    e200 = calc_ema(df['c'], 200).iloc[-1] if len(df) >= 200 else e50
    rsi_v = calc_rsi(df).iloc[-1]
    adx_v = calc_adx(df).iloc[-1] if len(df) >= 30 else 0
    vol_r = df['v'].iloc[-1] / (df['v'].iloc[-20:-1].mean() + 1e-10)
    
    buy_score = 0; sell_score = 0; confs = 0
    
    # 1. EMA Trend
    if price > e20 > e50: buy_score += 20; confs += 1
    elif price < e20 < e50: sell_score += 20; confs += 1
    
    # 2. RSI
    if 35 <= rsi_v <= 70:
        if price > e50: buy_score += 15; confs += 1
        else: sell_score += 15; confs += 1
        
    # 3. ADX
    if adx_v >= params['adx_min']:
        if price > e50: buy_score += 15; confs += 1
        else: sell_score += 15; confs += 1
        
    # 4. Volume
    if vol_r >= params['vol_mult']:
        if price > e50: buy_score += 15; confs += 1
        else: sell_score += 15; confs += 1

    direction = "SIDE"
    final_score = 0
    if buy_score > sell_score and confs >= params['confirmations']:
        direction = "UP"; final_score = buy_score
    elif sell_score > buy_score and confs >= params['confirmations']:
        direction = "DOWN"; final_score = sell_score
        
    if direction != "SIDE" and final_score >= params['min_score']:
        return {"signal": direction, "score": final_score, "justification": f"MicroMode Score:{final_score} Confs:{confs}"}
    return None

"""
    # Inserir antes da classe RiskManagerV4
    content = content.replace("class RiskManagerV4:", new_components + "\nclass RiskManagerV4:")

    # 2. Modificar o bot_loop para usar a nova lógica
    # Vamos substituir o bloco de busca de sinais
    old_signal_logic = """            # 3. PROCURA DE SINAIS COM CONFLUÊNCIA MTF
            sym_to_trade = None; side_to_trade = None; score_to_trade = None; ai_conf_to_trade = None
            
            with lock:
                if len(positions) < MAX_TRADES:
                    for sym in find_candidates():
                        if sym in positions: continue
                        df = get_candles(sym, "5m")
                        if df.empty or len(df) < 60: continue
                        
                        signal = hybrid_entry_signal(df)
                        if signal:
                            # CONFLUÊNCIA MULTI-TIMEFRAME (INSTITUCIONAL)
                            mtf_dir = get_mtf_confluence(sym)
                            if mtf_dir != signal["signal"]:
                                log(f"🚫 MTF Discorda: {sym} (Signal:{signal['signal']} | MTF:{mtf_dir})", level='reject')
                                continue
                            
                            features = ai_engine.extract_features(df)
                            ai_conf = 55.0; ai_dir = "uncertain"
                            if features and ai_engine.trained:
                                ai_conf, ai_dir = ai_engine.predict(features)
                                tech_dir = "bull" if signal["signal"] == "UP" else "bear"
                                if ai_dir != tech_dir and ai_conf > 65: continue
                            
                            if ai_conf < AI_MIN_CONFIDENCE and ai_engine.trained: continue
                            
                            log(f"💰 SINAL INSTITUCIONAL: {sym} {signal['signal']} | IA:{ai_conf:.0f}%", level='trade')
                            sym_to_trade = sym; side_to_trade = signal["signal"]
                            score_to_trade = signal; ai_conf_to_trade = ai_conf
                            break"""

    new_signal_logic = """            # 3. SELETIVIDADE ADAPTATIVA & BUSCA DE SINAIS
            current_bal = get_balance()
            params = AdaptiveSelector.get_params(current_bal)
            
            if not loss_guard.can_trade(current_bal, daily_loss, start_balance):
                time.sleep(60)
                continue

            sym_to_trade = None; side_to_trade = None; score_to_trade = None; ai_conf_to_trade = None
            
            with lock:
                if len(positions) < MAX_TRADES:
                    candidates = find_candidates()
                    for sym in candidates:
                        if sym in positions: continue
                        df = get_candles(sym, "5m")
                        if df.empty or len(df) < 60: continue
                        
                        # Selecionar função de sinal baseada no modo
                        if params['mode'] in ["MICRO", "ULTRA_SMALL"]:
                            signal = get_signal_micro_mode(df, params)
                        else:
                            signal = hybrid_entry_signal(df)
                            # Filtro MTF apenas para modos superiores
                            if signal and params['use_mtf']:
                                mtf_dir = get_mtf_confluence(sym)
                                if mtf_dir != signal["signal"]:
                                    log(f"🚫 MTF Discorda: {sym} (Signal:{signal['signal']} | MTF:{mtf_dir})", level='reject')
                                    continue
                        
                        if signal:
                            # Ajustar alavancagem e risco dinamicamente
                            global LEVERAGE, RISK
                            LEVERAGE = params['leverage']
                            RISK = params['risk_per_trade']
                            
                            ai_conf = 55.0
                            log(f"💰 SINAL {params['mode']}: {sym} {signal['signal']} | Score:{signal['score']}", level='trade')
                            sym_to_trade = sym; side_to_trade = signal["signal"]
                            score_to_trade = signal; ai_conf_to_trade = ai_conf
                            break"""

    content = content.replace(old_signal_logic, new_signal_logic)

    # 3. Atualizar o registro de trades no manage_positions para o LossGuard
    # Procurar onde o trade é salvo no histórico
    old_save_trade_call = "save_trade(trade_data)"
    new_save_trade_call = "save_trade(trade_data); loss_guard.register_trade(trade_data['pnl'])"
    content = content.replace(old_save_trade_call, new_save_trade_call)

    with open(path, "w") as f:
        f.write(content)
    print("Upgrade Adaptativo aplicado com sucesso!")

if __name__ == "__main__":
    apply_upgrade()
