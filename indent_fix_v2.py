
import os
import re

def fix_indentation_v2():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # Define o bloco manage_positions completo e limpo para evitar erros de indentação
    new_manage_positions = """
def manage_positions():
    global positions, daily_loss
    to_remove = []
    
    with lock:
        for symbol, pos in list(positions.items()):
            side = pos["side"]; entry = pos["entry"]; qty = pos["qty"]
            sl = pos["stop_loss"]; tp = pos["take_profit"]
            price = get_price(symbol)
            if not price: continue
            
            # Atualiza máximas/mínimas para Trailing
            if side == "UP": pos["highest_price"] = max(pos.get("highest_price", entry), price)
            else: pos["lowest_price"] = min(pos.get("lowest_price", entry), price)
            
            pnl_pct = (price - entry) / entry if side == "UP" else (entry - price) / entry
            close = False; reason = ""
            
            # 1. Trailing Stop Otimizado (Ativa com 1.2%)
            if pnl_pct > 0.012 and not pos.get('trailing_activated'):
                pos['trailing_activated'] = 1
                log(f"🛡️ Trailing Stop ativado para {symbol}", level='info')
            
            if pos.get('trailing_activated'):
                trail_price = pos.get("highest_price") * 0.997 if side == "UP" else pos.get("lowest_price") * 1.003
                if (side == "UP" and price < trail_price) or (side == "DOWN" and price > trail_price):
                    close = True; reason = "trailing_stop"
            
            # 2. TP / SL Virtual (Backup do Real)
            if not close:
                is_tp = (side == "UP" and price >= tp) or (side == "DOWN" and price <= tp)
                is_sl = (side == "UP" and price <= sl) or (side == "DOWN" and price >= sl)
                
                if is_tp:
                    close = True; reason = "take_profit"
                elif is_sl:
                    close = True; reason = "stop_loss"
            
            if close:
                cs_ = "SELL" if side == "UP" else "BUY"
                log(f"🚨 FECHAMENTO FORÇADO PELO BOT: {symbol} por {reason} | Preço: {price}", level='risk')
                try:
                    try: safe_req(client.futures_cancel_all_open_orders, symbol=symbol)
                    except: pass
                    order = safe_req(client.futures_create_order, symbol=symbol, side=cs_, type="MARKET", quantity=qty, reduceOnly=True)
                    final_price = float(order.get('avgPrice', 0)) or float(order.get('price', 0)) or price
                    real_pnl = (final_price - entry) * qty if side == "UP" else (entry - final_price) * qty
                    log(f"🏁 Posição {symbol} encerrada com sucesso via Bot Monitor. PnL: ${real_pnl:.2f}", level='success')
                    if real_pnl < 0: daily_loss += abs(real_pnl)
                    save_trade({
                        "symbol": symbol, "side": side, "entry_price": entry, "exit_price": final_price,
                        "quantity": qty, "pnl": real_pnl, "pnl_pct": pnl_pct,
                        "entry_time": pos["entry_time"], "exit_time": datetime.now().isoformat(),
                        "reason": f"bot_{reason}", "risk_used": pos.get("risk_used", 0.03),
                        "ai_confidence": pos.get("ai_confidence", 0), "score": pos.get("score", 0)
                    })
                    to_remove.append(symbol); delete_position(symbol)
                except Exception as e:
                    log(f"❌ FALHA CRÍTICA ao fechar {symbol}: {e}", level='error')
    
    with lock:
        for s in to_remove:
            if s in positions: del positions[s]
"""
    # Substituir a função manage_positions antiga pela nova
    content = re.sub(r'def manage_positions\(\):.*?for s in to_remove:.*?del positions\[s\]', new_manage_positions, content, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(content)
    print("Função manage_positions corrigida.")

if __name__ == "__main__":
    fix_indentation_v2()
