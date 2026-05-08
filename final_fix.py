
import os
import re

def apply_final_fix():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # 1. Melhorar execute_trade para incluir ordens LIMIT de TP reais
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
        
        # 2. Ordem de STOP LOSS REAL na Binance
        try:
            safe_req(client.futures_create_order, 
                     symbol=symbol, side=es_, type="STOP_MARKET", 
                     stopPrice=sl, quantity=qty, reduceOnly=True)
            log(f"🛡️ Stop Loss Real definido em {sl}", level='success')
        except Exception as e:
            log(f"⚠️ Falha ao definir SL real: {e}", level='warning')

        # 3. Ordem de TAKE PROFIT REAL na Binance (LIMIT)
        try:
            safe_req(client.futures_create_order, 
                     symbol=symbol, side=es_, type="LIMIT", 
                     price=tp, quantity=qty, timeInForce="GTC", reduceOnly=True)
            log(f"💰 Take Profit Real definido em {tp}", level='success')
        except Exception as e:
            log(f"⚠️ Falha ao definir TP real: {e}", level='warning')

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

    # Substituir execute_trade
    content = re.sub(r'def execute_trade\(symbol, side, score_data, ai_conf\):.*?log\(f"✅ TRADE ABERTO:.*?level=\'trade\'\)', new_execute_trade, content, flags=re.DOTALL)

    # 2. Melhorar get_active_positions para mostrar dados reais da Binance
    new_get_active_positions = """
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
                roe = (pnl / (entry * qty / LEVERAGE)) * 100 if entry > 0 else 0
                
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
"""
    # Substituir get_active_positions
    content = re.sub(r'async def get_active_positions\(\):.*?return {"positions": pos_list}', new_get_active_positions, content, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(content)
    print("Correções definitivas aplicadas no main.py")

if __name__ == "__main__":
    apply_final_fix()
