
import os

def fix_indentation():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        lines = f.readlines()

    new_lines = []
    in_bot_loop = False
    in_while = False
    
    for line in lines:
        if "def bot_loop():" in line:
            in_bot_loop = True
        
        if in_bot_loop and "while bot_on:" in line:
            in_while = True
            new_lines.append(line)
            continue
            
        if in_while and "# RESET DIÁRIO AUTOMÁTICO" in line:
            # Garante que o bloco de reset tenha 12 espaços de indentação (dentro do while)
            new_lines.append("            # RESET DIÁRIO AUTOMÁTICO\n")
            new_lines.append("            global last_reset_day\n")
            new_lines.append("            current_day = datetime.now().strftime(\"%Y-%m-%d\")\n")
            new_lines.append("            if last_reset_day != current_day:\n")
            new_lines.append("                log(f\"🌅 Novo dia detectado ({current_day}). Resetando limite diário.\", level='info')\n")
            new_lines.append("                daily_loss = 0.0\n")
            new_lines.append("                last_reset_day = current_day\n")
            new_lines.append("                save_state(\"daily_loss\", 0.0)\n")
            new_lines.append("                save_state(\"last_reset_day\", current_day)\n")
            new_lines.append("                start_balance = get_balance()\n")
            new_lines.append("                save_state(\"start_balance\", start_balance)\n")
            continue

        # Pula as linhas que foram inseridas incorretamente com indentação errada
        if in_while and any(x in line for x in ["last_reset_day = current_day", "save_state(\"daily_loss\", 0.0)", "save_state(\"last_reset_day\", current_day)"]):
            if "            " not in line[:12]: # Se não tiver a indentação correta, pula
                continue

        new_lines.append(line)

    with open(path, 'w') as f:
        f.writelines(new_lines)
    print("Indentação corrigida.")

if __name__ == "__main__":
    fix_indentation()
