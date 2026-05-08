#!/bin/bash
# Nostradamus Bot - Startup Script

# Carregar variáveis de ambiente do .env se existir
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Garantir que o diretório de logs e db existem
mkdir -p db logs

# Ativar ambiente virtual se existir, senão usar python3 direto
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Instalar dependências se necessário (silencioso)
pip install -r requirements.txt --quiet

# Iniciar o bot
echo "🚀 Iniciando Nostradamus v4.2.2..."
python3 main.py
