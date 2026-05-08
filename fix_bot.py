
import os
import re

def fix_main_py():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # 1. Melhorar o registro de histórico para garantir que PnL negativo seja registrado corretamente
    # A função save_trade já parece correta, mas vamos garantir que o real_pnl seja calculado com precisão.
    
    # 2. Corrigir a lógica de fechamento para usar ordens reais de SL/TP na Binance se possível, 
    # ou garantir que o monitoramento virtual seja infalível.
    
    # Vamos modificar a função execute_trade para enviar ordens de STOP_MARKET reais.
    # Isso garante que mesmo que o bot caia, a banca está protegida.
    
    new_execute_trade = """
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
            # Ajuste forçado para banca pequena para atingir o notional mínimo
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
        entry_price = float(order.get('avgPrice', price))
        
        # 2. Ordem de STOP LOSS REAL na Binance (Proteção Máxima)
        try:
            safe_req(client.futures_create_order, 
                     symbol=symbol, side=es_, type="STOP_MARKET", 
                     stopPrice=sl, quantity=qty, reduceOnly=True)
            log(f"🛡️ Stop Loss Real definido em {sl}", level='success')
        except Exception as e:
            log(f"⚠️ Falha ao definir SL real: {e}. Usando monitoramento virtual.", level='warning')

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
"""

    # Substituir a função execute_trade antiga pela nova
    content = re.sub(r'def execute_trade\(symbol, side, score_data, ai_conf\):.*?log\(f"✅ TRADE v4\.2 ABERTO:.*?level=\'trade\'\)', new_execute_trade, content, flags=re.DOTALL)

    # 3. Corrigir manage_positions para garantir que o trailing stop e o fechamento funcionem
    new_manage_positions = """
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
                try:
                    # Cancelar ordens pendentes antes de fechar a mercado
                    safe_req(client.futures_cancel_all_open_orders, symbol=symbol)
                    order = safe_req(client.futures_create_order, symbol=symbol, side=cs_, type="MARKET", quantity=qty, reduceOnly=True)
                    final_price = float(order.get('avgPrice', price))
                    real_pnl = (final_price - entry) * qty if side == "UP" else (entry - final_price) * qty
                    
                    log(f"🏁 Fechado: {symbol} | {reason} | PnL: ${real_pnl:.2f}", level='trade' if real_pnl > 0 else 'risk')
                    
                    if real_pnl < 0:
                        daily_loss += abs(real_pnl)
                    
                    save_trade({
                        "symbol": symbol, "side": side, "entry_price": entry, "exit_price": final_price,
                        "quantity": qty, "pnl": real_pnl, "pnl_pct": pnl_pct,
                        "entry_time": pos["entry_time"], "exit_time": datetime.now().isoformat(),
                        "reason": reason, "risk_used": pos.get("risk_used", 0.03),
                        "ai_confidence": pos.get("ai_confidence", 0), "score": pos.get("score", 0)
                    })
                    to_remove.append(symbol)
                    delete_position(symbol)
                except Exception as e:
                    log(f"❌ Erro ao fechar {symbol}: {e}", level='error')
                    
        except Exception as e:
            log(f"Erro manage {symbol}: {e}", level='error')
            
    if to_remove:
        with lock:
            for s in to_remove: positions.pop(s, None)
"""
    
    # Substituir manage_positions
    content = re.sub(r'def manage_positions\(\):.*?if to_remove:.*?for s in to_remove:.*?positions\.pop\(s, None\)', new_manage_positions, content, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(content)
    print("Correções aplicadas com sucesso no main.py")

if __name__ == "__main__":
    fix_main_py()
