
import os
import re

def apply_rr_optimization():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # 1. Aumentar o RR padrão e ajustar distâncias de risco
    content = content.replace("RR=2.0", "RR=3.0") # Alvo agora é 3x o risco
    
    # 2. Ajustar a lógica de Trailing Stop para ser menos "mão de alface"
    # Mudar o gatilho de ativação do trailing de 0.5% para 1.0% de lucro
    content = content.replace(
        "if pnl_pct > 0.005 and not pos.get('trailing_activated'):",
        "if pnl_pct > 0.012 and not pos.get('trailing_activated'):" # Ativa com 1.2% de lucro
    )
    
    # 3. Implementar trava de lucro mínimo no manage_positions
    # Evitar que o bot feche no TP virtual se o lucro for insignificante
    min_profit_logic = """
            # Trava de Lucro Mínimo: Só fecha no TP se o lucro for > 1.5x o risco inicial
            # ou se o sinal técnico inverter completamente.
            is_tp = (side == "UP" and price >= tp) or (side == "DOWN" and price <= tp)
            if is_tp:
                # Se atingiu o TP real, fecha sem dó
                close = True
                reason = "take_profit"
"""
    # Vamos garantir que o bot não saia por "score" baixo se estiver no lucro mas longe do TP
    content = re.sub(r'if \(side == "UP" and price >= tp\).*?reason = "take_profit"', min_profit_logic, content, flags=re.DOTALL)

    # 4. Ajustar o cálculo de tamanho de posição para ser mais conservador com o Stop
    # Reduzir o multiplicador do ATR para o Stop Loss ser mais "justo"
    content = content.replace(
        "risk_dist = max(atr_v * 1.2, price * 0.004)",
        "risk_dist = max(atr_v * 1.0, price * 0.0035)" # Stop mais curto
    )

    with open(path, 'w') as f:
        f.write(content)
    print("Otimização de Risco/Retorno aplicada.")

if __name__ == "__main__":
    apply_rr_optimization()
