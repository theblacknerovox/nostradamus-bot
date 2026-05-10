
import os
import re

def fix_direction_key_v2():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # O erro está acontecendo no bot_loop (linhas 1029 e 1036 conforme o grep)
    # A função hybrid_entry_signal retorna um dicionário onde a chave é "signal" e não "direction"
    # Vamos corrigir todas as referências a signal["direction"] no bot_loop
    
    content = content.replace('if mtf_dir != signal["direction"]:', 'if mtf_dir != signal["signal"]:')
    content = content.replace('tech_dir = "bull" if signal["direction"] == "UP" else "bear"', 'tech_dir = "bull" if signal["signal"] == "UP" else "bear"')
    content = content.replace('side_to_trade = signal["direction"]', 'side_to_trade = signal["signal"]')
    
    # Corrigir também o log que pode estar usando o nome errado
    content = content.replace('log(f"🚫 MTF Discorda: {sym} (Signal:{signal[\'direction\']}', 'log(f"🚫 MTF Discorda: {sym} (Signal:{signal[\'signal\']}')

    with open(path, 'w') as f:
        f.write(content)
    print("Correção V2 da chave 'direction' aplicada.")

if __name__ == "__main__":
    fix_direction_key_v2()
