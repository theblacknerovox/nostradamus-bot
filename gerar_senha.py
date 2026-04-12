#!/usr/bin/env python3
"""
Execute: python3 gerar_senha.py
Cole o resultado no .env como ADMIN_PASSWORD_HASH=...
"""
import bcrypt
import getpass

print("\n=== NOSTRADAMUS — Gerador de senha ===\n")
senha = getpass.getpass("Digite a senha que quer usar no dashboard: ")
confirmacao = getpass.getpass("Confirme a senha: ")

if senha != confirmacao:
    print("\n❌ Senhas não conferem. Tente novamente.\n")
    exit(1)

if len(senha) < 6:
    print("\n❌ Use pelo menos 6 caracteres.\n")
    exit(1)

hash_gerado = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

print("\n✅ Hash gerado com sucesso!\n")
print("Cole isso no seu arquivo .env:")
print(f"\nADMIN_PASSWORD_HASH={hash_gerado}\n")
