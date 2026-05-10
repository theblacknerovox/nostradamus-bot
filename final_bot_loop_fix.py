
import os

def fix_bot_loop():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # Define o bloco bot_loop completo e limpo
    new_bot_loop = """
def bot_loop():
    global bot_on, daily_loss, positions, start_balance, last_reset_day
    log("🚀 Nostradamus v4.2 iniciado! Ichimoku+VWAP+Fibonacci+IA", level='success')
    sync_positions()
    start_balance = get_balance()
    save_state("start_balance", start_balance)
    risk_manager.initial_balance = start_balance
    risk_manager.peak_balance = start_balance
    last_sync = time.time()
    last_train = time.time()
    last_reset_day = load_state("last_reset_day", "")
    
    while bot_on:
        try:
            bal = get_balance()
            if bal <= 0:
                log("Saldo zerado!", level='risk')
                bot_on = False
                break
            
            # RESET DIÁRIO AUTOMÁTICO
            current_day = datetime.now().strftime("%Y-%m-%d")
            if last_reset_day != current_day:
                log(f"🌅 Novo dia detectado ({current_day}). Resetando limite diário.", level='info')
                daily_loss = 0.0
                last_reset_day = current_day
                save_state("daily_loss", 0.0)
                save_state("last_reset_day", current_day)
                start_balance = bal
                save_state("start_balance", start_balance)

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
            
            sym_to_trade = None
            side_to_trade = None
            score_to_trade = None
            ai_conf_to_trade = None
            
            with lock:
                if len(positions) < MAX_TRADES:
                    for sym in find_candidates():
                        if sym in positions: continue
                        df = get_candles(sym, "5m")
                        if df.empty or len(df) < 60: continue
                        signal = hybrid_entry_signal(df)
                        if not signal: continue
                        
                        sd = {"score": signal["score"], "direction": signal["signal"], "signals": signal.get("signals", {})}
                        features = ai_engine.extract_features(df)
                        ai_conf = 55.0
                        ai_dir = "uncertain"
                        
                        if features and ai_engine.trained:
                            ai_conf, ai_dir = ai_engine.predict(features)
                            log(f"🧠 IA: {sym} → {ai_dir} ({ai_conf:.0f}%)", level='ai')
                            tech_dir = "bull" if sd["direction"] == "UP" else "bear"
                            if ai_dir != tech_dir and ai_conf > 65:
                                log(f"🧠 IA discorda: {sym}", level='ai')
                                continue
                        
                        if ai_conf < AI_MIN_CONFIDENCE and ai_engine.trained:
                            log(f"🧠 IA baixa: {sym} {ai_conf:.0f}%", level='ai')
                            continue
                        
                        log(f"💰 SINAL v4.2: {sym} {sd['direction']} | Score:{sd['score']} | IA:{ai_conf:.0f}%", level='trade')
                        sym_to_trade = sym; side_to_trade = sd["direction"]; score_to_trade = sd; ai_conf_to_trade = ai_conf
                        break
            
            if sym_to_trade:
                if check_order_book_liquidity(sym_to_trade, side_to_trade, 1.0):
                    execute_trade(sym_to_trade, side_to_trade, score_to_trade, ai_conf_to_trade)
                else:
                    log(f"🚫 Trade cancelado por falta de liquidez: {sym_to_trade}", level='reject')
                
            manage_positions()
            save_state("daily_loss", daily_loss)
            
        except Exception as e:
            log(f"Erro loop: {e}", level='error')
        
        time.sleep(INTERVAL)
"""
    import re
    # Substitui toda a função bot_loop antiga pela nova
    content = re.sub(r'def bot_loop\(\):.*?time\.sleep\(INTERVAL\)', new_bot_loop, content, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(content)
    print("Função bot_loop reescrita com sucesso.")

if __name__ == "__main__":
    fix_bot_loop()
