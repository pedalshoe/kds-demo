#!/usr/bin/env bash
# =============================================================================
# KDS-AI  —  start.sh
# Starts the full stack natively (no Docker required)
#
# Services started:
#   1. Ollama        — LLM server (llama3 + mistral + nomic-embed-text)
#   2. ChromaDB      — RAG vector store
#   3. n8n           — Workflow automation
#   4. Stripe CLI    — Webhook forwarding (optional)
#   5. Flask app     — KDS application
#
# Usage:
#   chmod +x start.sh
#   ./start.sh           # start all services
#   ./start.sh --no-n8n  # skip n8n
#   ./start.sh --no-stripe # skip Stripe CLI
# =============================================================================

set -e

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Config ────────────────────────────────────────────────────────────────────
APP_PORT=5001
CHROMA_PORT=8001
N8N_PORT=5678
OLLAMA_PORT=11434
LOG_DIR="./logs"
PID_FILE=".kds_pids"

# ── Flags ─────────────────────────────────────────────────────────────────────
RUN_N8N=true
RUN_STRIPE=true

for arg in "$@"; do
  case $arg in
    --no-n8n)    RUN_N8N=false ;;
    --no-stripe) RUN_STRIPE=false ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
log()     { echo -e "${CYAN}[KDS]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; }
header()  { echo -e "\n${BOLD}${BLUE}── $1 ──${NC}"; }

check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    error "$1 not found. Install it first."
    echo "    → $2"
    return 1
  fi
  return 0
}

wait_for_port() {
  local port=$1
  local name=$2
  local max=30
  local count=0
  while ! nc -z localhost "$port" 2>/dev/null; do
    sleep 1
    count=$((count + 1))
    if [ $count -ge $max ]; then
      error "$name did not start on port $port after ${max}s"
      return 1
    fi
  done
  success "$name ready on port $port"
}

# ── Cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
  echo ""
  log "Shutting down KDS-AI stack..."
  if [ -f "$PID_FILE" ]; then
    while IFS= read -r pid; do
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null && echo "  killed PID $pid"
      fi
    done < "$PID_FILE"
    rm -f "$PID_FILE"
  fi
  log "All services stopped. Goodbye."
}
trap cleanup EXIT INT TERM

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${BLUE}"
echo "  ██╗  ██╗██████╗ ███████╗       █████╗ ██╗"
echo "  ██║ ██╔╝██╔══██╗██╔════╝      ██╔══██╗██║"
echo "  █████╔╝ ██║  ██║███████╗█████╗███████║██║"
echo "  ██╔═██╗ ██║  ██║╚════██║╚════╝██╔══██║██║"
echo "  ██║  ██╗██████╔╝███████║      ██║  ██║██║"
echo "  ╚═╝  ╚═╝╚═════╝ ╚══════╝      ╚═╝  ╚═╝╚═╝"
echo -e "${NC}"
echo -e "  ${BOLD}Kitchen Display System — AI Enhanced${NC}"
echo -e "  LLM: llama3 + mistral  |  RAG: ChromaDB  |  Automation: n8n"
echo ""

# ── Pre-flight checks ─────────────────────────────────────────────────────────
header "Pre-flight checks"

check_cmd python3    "brew install python3"
check_cmd ollama     "brew install ollama  (https://ollama.com)"
check_cmd chroma     "pip install chromadb"

if $RUN_N8N; then
  check_cmd n8n      "npm install -g n8n" || RUN_N8N=false
fi

if $RUN_STRIPE; then
  check_cmd stripe   "brew install stripe/stripe-cli/stripe" || RUN_STRIPE=false
fi

# Check .env exists
if [ ! -f ".env" ]; then
  warn ".env not found — copying from .env.template"
  if [ -f ".env.template" ]; then
    cp .env.template .env
    warn "Edit .env and add your STRIPE_SECRET_KEY before continuing."
  else
    error ".env.template not found either. Please create .env manually."
    exit 1
  fi
fi

# Load .env
set -a
source .env
set +a

# Check venv
if [ ! -d ".venv" ]; then
  warn "No .venv found — creating one..."
  python3 -m venv .venv
fi

source .venv/bin/activate
success "Virtual environment active"

# Create log dir
mkdir -p "$LOG_DIR"
> "$PID_FILE"

# ── 1. Ollama ─────────────────────────────────────────────────────────────────
header "Starting Ollama"

if nc -z localhost $OLLAMA_PORT 2>/dev/null; then
  success "Ollama already running on port $OLLAMA_PORT"
else
  log "Starting Ollama server..."
  ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
  echo $! >> "$PID_FILE"
  wait_for_port $OLLAMA_PORT "Ollama"
fi

# Check required models
log "Checking models..."
for model in llama3 mistral nomic-embed-text; do
  if ollama list 2>/dev/null | grep -q "^${model}"; then
    success "Model ready: $model"
  else
    warn "Pulling model: $model (this may take a while...)"
    ollama pull "$model"
    success "Pulled: $model"
  fi
done

# ── 2. ChromaDB ───────────────────────────────────────────────────────────────
header "Starting ChromaDB"

if nc -z localhost $CHROMA_PORT 2>/dev/null; then
  success "ChromaDB already running on port $CHROMA_PORT"
else
  log "Starting ChromaDB vector store..."
  chroma run --path ./chroma_db --port $CHROMA_PORT > "$LOG_DIR/chroma.log" 2>&1 &
  echo $! >> "$PID_FILE"
  wait_for_port $CHROMA_PORT "ChromaDB"
fi

# ── 3. n8n ────────────────────────────────────────────────────────────────────
if $RUN_N8N; then
  header "Starting n8n"
  if nc -z localhost $N8N_PORT 2>/dev/null; then
    success "n8n already running on port $N8N_PORT"
  else
    log "Starting n8n workflow automation..."
    N8N_BASIC_AUTH_ACTIVE=true \
    N8N_BASIC_AUTH_USER=admin \
    N8N_BASIC_AUTH_PASSWORD=changeme \
    GENERIC_TIMEZONE=America/New_York \
    n8n start > "$LOG_DIR/n8n.log" 2>&1 &
    echo $! >> "$PID_FILE"
    wait_for_port $N8N_PORT "n8n"
  fi
fi

# ── 4. Stripe CLI ─────────────────────────────────────────────────────────────
if $RUN_STRIPE; then
  header "Starting Stripe CLI"
  log "Forwarding Stripe webhooks to localhost:${APP_PORT}/webhook/stripe"
  stripe listen \
    --forward-to "localhost:${APP_PORT}/webhook/stripe" \
    > "$LOG_DIR/stripe.log" 2>&1 &
  echo $! >> "$PID_FILE"
  sleep 2
  # Extract the webhook secret from the log
  WHSEC=$(grep -o 'whsec_[a-zA-Z0-9]*' "$LOG_DIR/stripe.log" | head -1)
  if [ -n "$WHSEC" ]; then
    success "Stripe CLI ready — webhook secret: $WHSEC"
    # Update .env with the live secret
    if grep -q "STRIPE_WEBHOOK_SECRET" .env; then
      sed -i.bak "s/STRIPE_WEBHOOK_SECRET=.*/STRIPE_WEBHOOK_SECRET=${WHSEC}/" .env
    else
      echo "STRIPE_WEBHOOK_SECRET=${WHSEC}" >> .env
    fi
    # Reload .env
    set -a; source .env; set +a
    success "Updated STRIPE_WEBHOOK_SECRET in .env"
  else
    warn "Could not auto-detect webhook secret — check logs/stripe.log"
  fi
fi

# ── 5. Flask app ──────────────────────────────────────────────────────────────
header "Starting KDS Flask App"

log "Installing/verifying dependencies..."
pip install -r requirements.txt -q

log "Starting Flask app on port ${APP_PORT}..."
FLASK_ENV=development \
python app.py > "$LOG_DIR/app.log" 2>&1 &
echo $! >> "$PID_FILE"
wait_for_port $APP_PORT "Flask app"

# ── Ready ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  KDS-AI Stack is LIVE${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}KDS Kitchen Display${NC}   →  http://localhost:${APP_PORT}"
echo -e "  ${BOLD}Chatbot / Order Entry${NC} →  http://localhost:${APP_PORT}/static/chat.html"
echo -e "  ${BOLD}Order Form${NC}            →  http://localhost:${APP_PORT}/static/order.html"
echo -e "  ${BOLD}KDS 3-Column Board${NC}    →  http://localhost:${APP_PORT}/static/kds.html"
if $RUN_N8N; then
echo -e "  ${BOLD}n8n Automation${NC}        →  http://localhost:${N8N_PORT}  (admin/changeme)"
fi
echo ""
echo -e "  ${BOLD}LLM Health${NC}            →  curl http://localhost:${APP_PORT}/api/llm/health"
echo -e "  ${BOLD}RAG Stats${NC}             →  curl http://localhost:${APP_PORT}/api/rag/stats"
echo ""
echo -e "  ${BOLD}Logs${NC}  →  ./logs/  (app.log, ollama.log, chroma.log, n8n.log)"
echo ""
echo -e "  ${YELLOW}Press Ctrl+C to stop all services${NC}"
echo ""

# ── Tail app log ──────────────────────────────────────────────────────────────
tail -f "$LOG_DIR/app.log"
