
import os
import re

def apply_binance_fix():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # 1. Corrigir o tipo de ordem de Stop Loss para ser aceito na API de Futuros
    # Em vez de STOP_MARKET, vamos usar STOP com price e stopPrice se necessário, 
    # ou garantir que os parâmetros de STOP_MARKET estejam corretos para o endpoint.
    # Na verdade, para Futuros Binance, STOP_MARKET requer stopPrice.
    
    # 2. Melhorar a precisão de preço para moedas com muitas casas decimais
    # Vamos ajustar a função adj_price para ser mais robusta.
    new_adj_price = """
def adj_price(symbol, price):
    if symbol not in symbol_filters: return round(price, 8)
    f = symbol_filters[symbol]
    tick = f["tick"]
    # Cálculo de precisão mais robusto
    prec = max(0, int(round(-math.log10(tick)))) if tick > 0 else 8
    # Arredondar para o tick size correto
    price = round(price / tick) * tick
    return float(f"{price:.{prec}f}")
"""
    content = re.sub(r'def adj_price\(symbol,price\):.*?return round\(max\(min\(price,f\["max_price"\]\),f\["min_price"\]\),prec\)', new_adj_price, content, flags=re.DOTALL)

    # 3. Corrigir o envio de ordens de TP/SL na função execute_trade
    # Usar parâmetros explícitos e tratar o erro de Margin Type
    new_execution_logic = """
        # EXECUÇÃO NA BINANCE
        try:
            safe_req(client.futures_change_leverage, symbol=symbol, leverage=LEVERAGE)
            # Mudar tipo de margem apenas se necessário para evitar erro -4046
            pos_info = safe_req(client.futures_position_information, symbol=symbol)
            if pos_info and pos_info[0].get('marginType') != 'isolated':
                try: safe_req(client.futures_change_margin_type, symbol=symbol, marginType="ISOLATED")
                except: pass
        except: pass
            
        # 1. Ordem Principal
        order = safe_req(client.futures_create_order, symbol=symbol, side=os_, type="MARKET", quantity=qty)
        entry_price = float(order.get('avgPrice', 0)) or float(order.get('price', 0)) or price
        
        # 2. Ordem de STOP LOSS REAL na Binance
        try:
            # Para STOP_MARKET em Futuros, usamos stopPrice
            safe_req(client.futures_create_order, 
                     symbol=symbol, side=es_, type="STOP_MARKET", 
                     stopPrice=sl, quantity=qty, reduceOnly=True, workingType="MARK_PRICE")
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
"""
    content = re.sub(r'# EXECUÇÃO NA BINANCE.*?log\(f"💰 Take Profit Real definido em {tp}", level=\'success\'\)\s+except Exception as e:\s+log\(f"⚠️ Falha ao definir TP real {symbol}: {e}", level=\'error\'\)', new_execution_logic, content, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(content)
    print("Correções de API e precisão aplicadas.")

if __name__ == "__main__":
    apply_binance_fix()
