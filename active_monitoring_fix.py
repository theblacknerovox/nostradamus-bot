
import os
import re

def apply_active_monitoring():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # 1. Garantir que o manage_positions seja chamado com mais frequência e seja mais agressivo
    # Já temos a lógica de fecho no manage_positions, mas vamos garantir que ela seja infalível
    # e que use o preço atual de mercado para decidir o fecho, independente de ordens na Binance.

    # 2. Criar uma thread separada apenas para monitoramento ultra-rápido de TP/SL
    monitoring_thread_code = """
def fast_monitor_loop():
    log("🚀 Monitoramento Ultra-Rápido de TP/SL iniciado (1s)", level='info')
    while True:
        try:
            if bot_on:
                manage_positions()
        except Exception as e:
            log(f"Erro no monitoramento rápido: {e}", level='error')
        time.sleep(1) # Verifica a cada 1 segundo
"""

    # Inserir a thread de monitoramento rápido antes do on_startup
    if "def fast_monitor_loop():" not in content:
        content = content.replace("def scanner_loop():", monitoring_thread_code + "\ndef scanner_loop():")

    # Iniciar a thread no on_startup
    if "threading.Thread(target=fast_monitor_loop, daemon=True).start()" not in content:
        content = content.replace(
            "threading.Thread(target=scanner_loop, daemon=True).start()",
            "threading.Thread(target=scanner_loop, daemon=True).start()\n    threading.Thread(target=fast_monitor_loop, daemon=True).start()"
        )

    # 3. Ajustar o manage_positions para ser mais robusto no fecho forçado
    # Vamos garantir que ele feche a posição mesmo que a ordem de mercado falhe na primeira tentativa
    robust_close_logic = """
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
"""
    # Substituir a lógica de fecho no manage_positions
    content = re.sub(r'if close:.*?delete_position\(symbol\)', robust_close_logic, content, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(content)
    print("Monitoramento ativo interno implementado com sucesso.")

if __name__ == "__main__":
    apply_active_monitoring()
