
import os
import re

def fix_institutional_bot_loop():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    new_bot_loop = """
def bot_loop():
    global bot_on, daily_loss, positions, start_balance, last_reset_day
    log("🚀 Nostradamus v4.3 INSTITUCIONAL iniciado!", level='success')
    sync_positions()
    start_balance = get_balance()
    save_state("start_balance", start_balance)
    last_sync = time.time()
    last_train = time.time()
    last_reset_day = load_state("last_reset_day", "")
    
    while bot_on:
        try:
            # 1. FILTROS INSTITUCIONAIS DE SEGURANÇA
            if not should_operate_equity_filter():
                log("🛡️ Equity Curve Filter: Pausando por sequência de perdas.", level='warning')
                time.sleep(300); continue

            current_bal = get_balance()
            peak = load_state("peak_balance", start_balance)
            if current_bal > peak:
                peak = current_bal; save_state("peak_balance", peak)
            
            drawdown = (peak - current_bal) / peak if peak > 0 else 0
            risk_mult = drawdown_manager.get_risk_multiplier(drawdown)
            if risk_mult == 0:
                log(f"🚨 Drawdown Crítico ({drawdown:.1%}): Suspenso.", level='risk')
                time.sleep(3600); continue

            # 2. RESET DIÁRIO
            current_day = datetime.now().strftime("%Y-%m-%d")
            if last_reset_day != current_day:
                log(f"🌅 Novo dia ({current_day}). Resetando limites.", level='info')
                daily_loss = 0.0; last_reset_day = current_day; bot_on = True
                save_state("daily_loss", 0.0); save_state("last_reset_day", current_day)
                save_state("bot_on", True); start_balance = current_bal
                save_state("start_balance", start_balance)

            if daily_loss > start_balance * DAILY_LOSS_LIMIT:
                log(f"Stop diário! ${daily_loss:.2f}", level='risk')
                bot_on = False; break
            
            if time.time() - last_sync > 300:
                sync_positions(); last_sync = time.time()
            
            # 3. PROCURA DE SINAIS COM CONFLUÊNCIA MTF
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
                            if mtf_dir != signal["direction"]:
                                log(f"🚫 MTF Discorda: {sym} (Signal:{signal['direction']} | MTF:{mtf_dir})", level='reject')
                                continue
                            
                            features = ai_engine.extract_features(df)
                            ai_conf = 55.0; ai_dir = "uncertain"
                            if features and ai_engine.trained:
                                ai_conf, ai_dir = ai_engine.predict(features)
                                tech_dir = "bull" if signal["direction"] == "UP" else "bear"
                                if ai_dir != tech_dir and ai_conf > 65: continue
                            
                            if ai_conf < AI_MIN_CONFIDENCE and ai_engine.trained: continue
                            
                            log(f"💰 SINAL INSTITUCIONAL: {sym} {signal['direction']} | IA:{ai_conf:.0f}%", level='trade')
                            sym_to_trade = sym; side_to_trade = signal["direction"]
                            score_to_trade = signal; ai_conf_to_trade = ai_conf
                            break
            
            if sym_to_trade:
                if check_order_book_liquidity(sym_to_trade, side_to_trade, 1.0):
                    execute_trade(sym_to_trade, side_to_trade, score_to_trade, ai_conf_to_trade)
                
            manage_positions()
            save_state("daily_loss", daily_loss)
            
        except Exception as e:
            log(f"Erro loop: {e}", level='error')
        time.sleep(INTERVAL)
"""
    # Substituir a função bot_loop antiga pela nova institucional
    content = re.sub(r'def bot_loop\(\):.*?time\.sleep\(INTERVAL\)', new_bot_loop, content, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(content)
    print("Bot Loop Institucional corrigido.")

if __name__ == "__main__":
    fix_institutional_bot_loop()
