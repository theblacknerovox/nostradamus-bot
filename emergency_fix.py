
import os
import re

def apply_emergency_fix():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # 1. Corrigir o erro onde entry_price pode vir como 0.0 da Binance
    # Na função execute_trade, garantir que o entry_price seja válido
    content = content.replace(
        "entry_price = float(order.get('avgPrice', price))",
        "entry_price = float(order.get('avgPrice', 0)) or float(order.get('price', 0)) or price"
    )

    # 2. Corrigir o erro de divisão por zero no cálculo do ROE
    content = content.replace(
        "roe = (pnl / (entry * qty / LEVERAGE)) * 100 if entry > 0 else 0",
        "roe = (pnl / (max(0.01, entry * qty / LEVERAGE))) * 100 if entry > 0 else 0"
    )

    # 3. Adicionar logs extras para depurar falhas de ordens TP/SL
    content = content.replace(
        "log(f\"⚠️ Falha ao definir SL real: {e}\", level='warning')",
        "log(f\"⚠️ Falha ao definir SL real {symbol}: {e}\", level='error')"
    )
    content = content.replace(
        "log(f\"⚠️ Falha ao definir TP real: {e}\", level='warning')",
        "log(f\"⚠️ Falha ao definir TP real {symbol}: {e}\", level='error')"
    )

    with open(path, 'w') as f:
        f.write(content)
    print("Correções de emergência aplicadas no main.py")

if __name__ == "__main__":
    apply_emergency_fix()
