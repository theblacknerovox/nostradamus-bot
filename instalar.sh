#!/bin/bash
# =============================================
# NOSTRADAMUS BOT — Instalador automático
# Testado em Ubuntu 22.04 (Hostinger VPS)
# Execute: bash instalar.sh
# =============================================

set -e

VERDE='\033[0;32m'
AMARELO='\033[1;33m'
VERMELHO='\033[0;31m'
NC='\033[0m'

ok()  { echo -e "${VERDE}✅ $1${NC}"; }
info(){ echo -e "${AMARELO}➜  $1${NC}"; }
err() { echo -e "${VERMELHO}❌ $1${NC}"; exit 1; }

echo ""
echo "================================================"
echo "   NOSTRADAMUS BOT v3.2.0 — Instalador"
echo "================================================"
echo ""

# Verifica se é root ou tem sudo
if [ "$EUID" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

# 1. Atualiza o sistema
info "Atualizando o sistema..."
$SUDO apt-get update -qq && $SUDO apt-get upgrade -y -qq
ok "Sistema atualizado"

# 2. Instala dependências do sistema
info "Instalando dependências (git, curl, python3, pip)..."
$SUDO apt-get install -y -qq \
    git curl wget \
    python3 python3-pip python3-venv \
    ca-certificates gnupg lsb-release
ok "Dependências instaladas"

# 3. Instala Docker
if ! command -v docker &> /dev/null; then
    info "Instalando Docker..."
    $SUDO mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu \
        $(lsb_release -cs) stable" | \
        $SUDO tee /etc/apt/sources.list.d/docker.list > /dev/null
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    $SUDO systemctl enable docker
    $SUDO systemctl start docker
    ok "Docker instalado"
else
    ok "Docker já instalado"
fi

# 4. Instala Docker Compose (standalone)
if ! command -v docker-compose &> /dev/null; then
    info "Instalando Docker Compose..."
    $SUDO curl -SL \
        "https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-linux-x86_64" \
        -o /usr/local/bin/docker-compose
    $SUDO chmod +x /usr/local/bin/docker-compose
    ok "Docker Compose instalado"
else
    ok "Docker Compose já instalado"
fi

# 5. Cria pasta do projeto
PROJECT_DIR="$HOME/nostradamus"
info "Criando pasta do projeto em $PROJECT_DIR..."
mkdir -p "$PROJECT_DIR/data"
ok "Pasta criada"

# 6. Copia arquivos para o projeto (se estiver rodando do diretório com os arquivos)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
info "Copiando arquivos do projeto..."
for f in main.py requirements.txt Dockerfile docker-compose.yml dashboard.html gerar_senha.py .env.example; do
    if [ -f "$SCRIPT_DIR/$f" ]; then
        cp "$SCRIPT_DIR/$f" "$PROJECT_DIR/$f"
    fi
done
ok "Arquivos copiados"

# 7. Gera o arquivo .env se não existir
if [ ! -f "$PROJECT_DIR/.env" ]; then
    info "Criando .env a partir do exemplo..."
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo ""
    echo -e "${AMARELO}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${AMARELO}  AÇÃO NECESSÁRIA: Configure seu .env${NC}"
    echo -e "${AMARELO}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  1. Gere sua senha:"
    echo "     cd $PROJECT_DIR && python3 gerar_senha.py"
    echo ""
    echo "  2. Edite o .env:"
    echo "     nano $PROJECT_DIR/.env"
    echo ""
    echo "  3. Preencha:"
    echo "     BINANCE_API_KEY=..."
    echo "     BINANCE_SECRET_KEY=..."
    echo "     ADMIN_PASSWORD_HASH=..."
    echo "     JWT_SECRET=..."
    echo ""
    echo "  4. Depois suba o bot:"
    echo "     cd $PROJECT_DIR && docker-compose up -d"
    echo ""
    echo -e "${AMARELO}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
else
    ok ".env já existe"
fi

# 8. Instala python deps localmente para gerar senha
info "Instalando bcrypt para geração de senha..."
pip3 install bcrypt -q 2>/dev/null || true
ok "bcrypt instalado"

# 9. Configura firewall (UFW)
if command -v ufw &> /dev/null; then
    info "Configurando firewall..."
    $SUDO ufw allow 22/tcp   > /dev/null 2>&1 || true
    $SUDO ufw allow 8000/tcp > /dev/null 2>&1 || true
    $SUDO ufw --force enable > /dev/null 2>&1 || true
    ok "Firewall configurado (portas 22 e 8000 abertas)"
fi

echo ""
echo -e "${VERDE}================================================${NC}"
echo -e "${VERDE}   Instalação concluída!${NC}"
echo -e "${VERDE}================================================${NC}"
echo ""
echo "Próximos passos:"
echo "  cd $PROJECT_DIR"
echo "  python3 gerar_senha.py      # gera o hash da senha"
echo "  nano .env                   # preenche as chaves"
echo "  docker-compose up -d        # sobe o bot"
echo "  docker-compose logs -f      # acompanha os logs"
echo ""
echo "Dashboard: http://IP_DO_SEU_VPS:8000"
echo ""
