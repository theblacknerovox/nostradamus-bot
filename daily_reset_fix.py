
import os
import re

def apply_daily_reset_fix():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # 1. Adicionar variável para rastrear o último dia de reset
    if "last_reset_day = load_state(\"last_reset_day\", \"\")" not in content:
        content = content.replace(
            "daily_loss = load_state(\"daily_loss\", 0.0)",
            "daily_loss = load_state(\"daily_loss\", 0.0)\nlast_reset_day = load_state(\"last_reset_day\", \"\")"
        )

    # 2. Implementar a lógica de reset no bot_loop
    reset_logic = """
            # RESET DIÁRIO AUTOMÁTICO
            global last_reset_day
            current_day = datetime.now().strftime("%Y-%m-%d")
            if last_reset_day != current_day:
                log(f"🌅 Novo dia detectado ({current_day}). Resetando limite diário.", level='info')
                daily_loss = 0.0
                last_reset_day = current_day
                save_state("daily_loss", 0.0)
                save_state("last_reset_day", current_day)
                # Atualizar saldo inicial para o novo dia
                start_balance = bal
                save_state("start_balance", start_balance)
"""
    
    # Inserir a lógica de reset logo após o início do loop while bot_on
    if "RESET DIÁRIO AUTOMÁTICO" not in content:
        content = content.replace(
            "while bot_on:",
            "while bot_on:" + reset_logic
        )

    with open(path, 'w') as f:
        f.write(content)
    print("Lógica de reset diário automático implementada.")

if __name__ == "__main__":
    apply_daily_reset_fix()
