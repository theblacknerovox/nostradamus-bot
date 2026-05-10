
import os
import re

def fix_direction_key():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        content = f.read()

    # Corrigir a referência de signal["direction"] para signal["signal"]
    # que é o nome correto retornado pela função hybrid_entry_signal
    content = content.replace('if mtf_dir != signal["direction"]:', 'if mtf_dir != signal["signal"]:')
    content = content.replace('side_to_trade = signal["direction"]', 'side_to_trade = signal["signal"]')
    
    # Também garantir que o log use o nome correto
    content = content.replace('log(f"💰 SINAL INSTITUCIONAL: {sym} {signal[\'direction\']}', 'log(f"💰 SINAL INSTITUCIONAL: {sym} {signal[\'signal\']}')

    with open(path, 'w') as f:
        f.write(content)
    print("Erro de chave 'direction' corrigido.")

if __name__ == "__main__":
    fix_direction_key()
