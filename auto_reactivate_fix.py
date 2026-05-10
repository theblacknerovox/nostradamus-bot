
import os
import re

def apply_auto_reactivate():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # Modificar a lógica de reset diário para incluir a reativação do bot
    old_reset = """            if last_reset_day != current_day:
                log(f"🌅 Novo dia detectado ({current_day}). Resetando limite diário.", level='info')
                daily_loss = 0.0
                last_reset_day = current_day
                save_state("daily_loss", 0.0)
                save_state("last_reset_day", current_day)
                start_balance = bal
                save_state("start_balance", start_balance)"""
                
    new_reset = """            if last_reset_day != current_day:
                log(f"🌅 Novo dia detectado ({current_day}). Resetando limite diário e reativando bot.", level='info')
                daily_loss = 0.0
                last_reset_day = current_day
                bot_on = True # REATIVAÇÃO AUTOMÁTICA
                save_state("daily_loss", 0.0)
                save_state("last_reset_day", current_day)
                save_state("bot_on", True)
                start_balance = bal
                save_state("start_balance", start_balance)"""

    if old_reset in content:
        content = content.replace(old_reset, new_reset)
    else:
        # Tenta uma substituição mais genérica se a indentação for diferente
        content = re.sub(r'if last_reset_day != current_day:.*?save_state\("start_balance", start_balance\)', new_reset, content, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(content)
    print("Reativação automática implementada.")

if __name__ == "__main__":
    apply_auto_reactivate()
