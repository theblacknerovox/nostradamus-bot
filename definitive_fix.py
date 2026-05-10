
import os

def definitive_fix():
    path = '/home/ubuntu/nostradamus-bot/main.py'
    with open(path, 'r') as f:
        lines = f.readlines()

    new_lines = []
    skip_until = -1
    
    for i, line in enumerate(lines):
        if i <= skip_until:
            continue
            
        if "def manage_positions():" in line:
            # Insere a função manage_positions inteira e limpa
            new_lines.append("def manage_positions():\n")
            new_lines.append("    global positions, daily_loss\n")
            new_lines.append("    to_remove = []\n")
            new_lines.append("    with lock:\n")
            new_lines.append("        for symbol, pos in list(positions.items()):\n")
            new_lines.append("            side = pos[\"side\"]; entry = pos[\"entry\"]; qty = pos[\"qty\"]\n")
            new_lines.append("            sl = pos[\"stop_loss\"]; tp = pos[\"take_profit\"]\n")
            new_lines.append("            price = get_price(symbol)\n")
            new_lines.append("            if not price: continue\n")
            new_lines.append("            if side == \"UP\": pos[\"highest_price\"] = max(pos.get(\"highest_price\", entry), price)\n")
            new_lines.append("            else: pos[\"lowest_price\"] = min(pos.get(\"lowest_price\", entry), price)\n")
            new_lines.append("            pnl_pct = (price - entry) / entry if side == \"UP\" else (entry - price) / entry\n")
            new_lines.append("            close = False; reason = \"\"\n")
            new_lines.append("            if pnl_pct > 0.012 and not pos.get('trailing_activated'):\n")
            new_lines.append("                pos['trailing_activated'] = 1\n")
            new_lines.append("                log(f\"🛡️ Trailing Stop ativado para {symbol}\", level='info')\n")
            new_lines.append("            if pos.get('trailing_activated'):\n")
            new_lines.append("                trail_price = pos.get(\"highest_price\") * 0.997 if side == \"UP\" else pos.get(\"lowest_price\") * 1.003\n")
            new_lines.append("                if (side == \"UP\" and price < trail_price) or (side == \"DOWN\" and price > trail_price):\n")
            new_lines.append("                    close = True; reason = \"trailing_stop\"\n")
            new_lines.append("            if not close:\n")
            new_lines.append("                is_tp = (side == \"UP\" and price >= tp) or (side == \"DOWN\" and price <= tp)\n")
            new_lines.append("                is_sl = (side == \"UP\" and price <= sl) or (side == \"DOWN\" and price >= sl)\n")
            new_lines.append("                if is_tp:\n")
            new_lines.append("                    close = True; reason = \"take_profit\"\n")
            new_lines.append("                elif is_sl:\n")
            new_lines.append("                    close = True; reason = \"stop_loss\"\n")
            new_lines.append("            if close:\n")
            new_lines.append("                cs_ = \"SELL\" if side == \"UP\" else \"BUY\"\n")
            new_lines.append("                log(f\"🚨 FECHAMENTO FORÇADO PELO BOT: {symbol} por {reason} | Preço: {price}\", level='risk')\n")
            new_lines.append("                try:\n")
            new_lines.append("                    try: safe_req(client.futures_cancel_all_open_orders, symbol=symbol)\n")
            new_lines.append("                    except: pass\n")
            new_lines.append("                    order = safe_req(client.futures_create_order, symbol=symbol, side=cs_, type=\"MARKET\", quantity=qty, reduceOnly=True)\n")
            new_lines.append("                    final_price = float(order.get('avgPrice', 0)) or float(order.get('price', 0)) or price\n")
            new_lines.append("                    real_pnl = (final_price - entry) * qty if side == \"UP\" else (entry - final_price) * qty\n")
            new_lines.append("                    log(f\"🏁 Posição {symbol} encerrada com sucesso via Bot Monitor. PnL: ${real_pnl:.2f}\", level='success')\n")
            new_lines.append("                    if real_pnl < 0: daily_loss += abs(real_pnl)\n")
            new_lines.append("                    save_trade({\n")
            new_lines.append("                        \"symbol\": symbol, \"side\": side, \"entry_price\": entry, \"exit_price\": final_price,\n")
            new_lines.append("                        \"quantity\": qty, \"pnl\": real_pnl, \"pnl_pct\": pnl_pct,\n")
            new_lines.append("                        \"entry_time\": pos[\"entry_time\"], \"exit_time\": datetime.now().isoformat(),\n")
            new_lines.append("                        \"reason\": f\"bot_{reason}\", \"risk_used\": pos.get(\"risk_used\", 0.03),\n")
            new_lines.append("                        \"ai_confidence\": pos.get(\"ai_confidence\", 0), \"score\": pos.get(\"score\", 0)\n")
            new_lines.append("                    })\n")
            new_lines.append("                    to_remove.append(symbol); delete_position(symbol)\n")
            new_lines.append("                except Exception as e:\n")
            new_lines.append("                    log(f\"❌ FALHA CRÍTICA ao fechar {symbol}: {e}\", level='error')\n")
            new_lines.append("    with lock:\n")
            new_lines.append("        for s in to_remove:\n")
            new_lines.append("            if s in positions: del positions[s]\n")
            
            # Encontra o fim da função antiga para pular
            for j in range(i + 1, len(lines)):
                if "def " in lines[j] or "@app." in lines[j]:
                    skip_until = j - 1
                    break
            continue
        
        new_lines.append(line)

    with open(path, 'w') as f:
        f.writelines(new_lines)
    print("Correção definitiva aplicada.")

if __name__ == "__main__":
    definitive_fix()
