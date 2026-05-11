#!/usr/bin/env bash
# =============================================================================
# KDS-AI  —  start-docker-local-ollama.sh
# Starts the Docker stack except Ollama, using Ollama running natively on macOS.
#
# Services:
#   kds-app   — Flask / gunicorn (port 8000 internal, 80 via nginx)
#   chromadb  — RAG vector store
#   nginx     — Reverse proxy + WebSocket (port 80)
#   n8n       — Workflow automation (port 5678)
#
# External dependency:
#   Ollama    — native macOS process on localhost:11434
#
# Usage:
#   chmod +x start-docker-local-ollama.sh
#   ./start-docker-local-ollama.sh
#   ./start-docker-local-ollama.sh --build
#   ./start-docker-local-ollama.sh --clean
#   ./start-docker-local-ollama.sh --stop
#   ./start-docker-local-ollama.sh --logs
#   ./start-docker-local-ollama.sh --status
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

BASE_COMPOSE_FILE="docker-compose.yml"
OVERRIDE_COMPOSE_FILE="docker-compose.local-ollama.yml"
COMPOSE_CMD=(docker compose -f "$BASE_COMPOSE_FILE" -f "$OVERRIDE_COMPOSE_FILE")
MODELS=("llama3.1" "mistral" "nomic-embed-text")
APP_PORT=80
N8N_PORT=5678
OLLAMA_PORT=11434
N8N_WORKFLOW_FILE="/home/node/.n8n/workflows/kds_workflow.json"

BUILD=false
CLEAN=false
STOP=false
SHOW_LOGS=false
STATUS=false

for arg in "$@"; do
  case $arg in
    --build)  BUILD=true ;;
    --clean)  CLEAN=true ;;
    --stop)   STOP=true ;;
    --logs)   SHOW_LOGS=true ;;
    --status) STATUS=true ;;
  esac
done

log()     { echo -e "${CYAN}[KDS]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }
header()  { echo -e "\n${BOLD}${BLUE}── $1 ──${NC}"; }

seed_n8n_workflow() {
  if [ ! -f "./n8n/kds_workflow.json" ]; then
    warn "n8n/kds_workflow.json not found — skipping workflow import"
    return 0
  fi

  header "Seeding n8n workflow"
  log "Importing n8n/kds_workflow.json into a clean n8n database..."
  "${COMPOSE_CMD[@]}" exec -u node n8n n8n import:workflow --input="$N8N_WORKFLOW_FILE"

  log "Activating imported workflow..."
  "${COMPOSE_CMD[@]}" exec -u node n8n n8n update:workflow --all --active=true

  log "Restarting n8n so imported workflow activation takes effect..."
  "${COMPOSE_CMD[@]}" restart n8n
  sleep 3

  success "n8n workflow imported from n8n/kds_workflow.json"
}

check_native_ollama() {
  header "Checking native Ollama"

  if ! command -v ollama &>/dev/null; then
    error "Native Ollama is not installed. Install it first from https://ollama.com/download"
  fi

  if ! curl -fsS --max-time 5 "http://localhost:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
    error "Native Ollama is not responding on http://localhost:${OLLAMA_PORT}. Start it on macOS first."
  fi
  success "Native Ollama is reachable on localhost:${OLLAMA_PORT}"

  for model in "${MODELS[@]}"; do
    if ollama list 2>/dev/null | grep -q "^${model}"; then
      success "Model ready: $model"
    else
      warn "Model missing in native Ollama: $model"
    fi
  done
}

echo ""
echo -e "${BOLD}${BLUE}"
echo "  ██╗  ██╗██████╗ ███████╗       █████╗ ██╗"
echo "  ██║ ██╔╝██╔══██╗██╔════╝      ██╔══██╗██║"
echo "  █████╔╝ ██║  ██║███████╗█████╗███████║██║"
echo "  ██╔═██╗ ██║  ██║╚════██║╚════╝██╔══██║██║"
echo "  ██║  ██╗██████╔╝███████║      ██║  ██║██║"
echo "  ╚═╝  ╚═╝╚═════╝ ╚══════╝      ╚═╝  ╚═╝╚═╝"
echo -e "${NC}"
echo -e "  ${BOLD}Kitchen Display System — Docker + Native Ollama${NC}"
echo -e "  nginx · Flask · ChromaDB · n8n  |  Ollama on macOS"
echo ""

if $STOP; then
  header "Stopping KDS-AI Docker Stack"
  "${COMPOSE_CMD[@]}" down
  success "Docker services stopped"
  exit 0
fi

if $SHOW_LOGS; then
  "${COMPOSE_CMD[@]}" logs -f
  exit 0
fi

if $STATUS; then
  header "KDS-AI Service Status"
  "${COMPOSE_CMD[@]}" ps
  exit 0
fi

header "Pre-flight checks"

if ! docker info &>/dev/null; then
  error "Docker daemon is not running. Start Docker Desktop first."
fi
success "Docker daemon running"

if ! docker compose version &>/dev/null; then
  error "docker compose not found. Update Docker Desktop to v2.x+"
fi
success "docker compose available"

if [ ! -f ".env" ]; then
  warn ".env not found — copying from .env.example"
  if [ -f ".env.example" ]; then
    cp .env.example .env
    warn "Edit .env with your STRIPE_SECRET_KEY before continuing."
    exit 1
  else
    error ".env.example not found. Create .env manually."
  fi
fi
success ".env found"

if [ ! -f "$BASE_COMPOSE_FILE" ]; then
  error "$BASE_COMPOSE_FILE not found. Run from repo root."
fi
if [ ! -f "$OVERRIDE_COMPOSE_FILE" ]; then
  error "$OVERRIDE_COMPOSE_FILE not found. Run from repo root."
fi
success "Compose files found"

check_native_ollama

if $CLEAN; then
  header "Clean mode — wiping Docker volumes"
  warn "This will delete all ChromaDB data and n8n data in Docker."
  read -r -p "  Are you sure? (y/N) " confirm
  if [[ "$confirm" =~ ^[Yy]$ ]]; then
    "${COMPOSE_CMD[@]}" down -v --remove-orphans
    rm -rf ./chroma_db/
    success "Docker volumes and local chroma_db wiped"
    BUILD=true
  else
    log "Clean cancelled"
    exit 0
  fi
fi

if $BUILD; then
  header "Building kds-app image"
  "${COMPOSE_CMD[@]}" build kds-app
  success "kds-app image built"
fi

header "Starting core services"
log "Bringing up: chromadb, n8n..."
"${COMPOSE_CMD[@]}" up -d chromadb n8n
success "chromadb and n8n started"

if $CLEAN; then
  seed_n8n_workflow
fi

header "Starting kds-app and nginx"
"${COMPOSE_CMD[@]}" up -d --no-deps kds-app nginx
sleep 3

header "Verifying services"
services=("kds-app" "chromadb" "nginx" "n8n")
all_ok=true

for svc in "${services[@]}"; do
  state=$("${COMPOSE_CMD[@]}" ps --format json "$svc" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('State','unknown'))" 2>/dev/null || echo "unknown")
  if [ "$state" = "running" ]; then
    success "$svc — running"
  else
    warn "$svc — $state (check: ${COMPOSE_CMD[*]} logs $svc)"
    all_ok=false
  fi
done

header "LLM health check"
sleep 2
response=$(curl -s --max-time 10 "http://localhost/api/llm/health" 2>/dev/null || echo "")
if echo "$response" | grep -q '"status":"ok"'; then
  success "LLM orchestrator online — native Ollama responding"
elif echo "$response" | grep -q "unavailable"; then
  warn "LLM orchestrator initializing — check: ${COMPOSE_CMD[*]} logs -f kds-app"
else
  warn "LLM health endpoint unreachable — nginx or kds-app may still be starting"
fi

echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  KDS-AI Docker Stack is LIVE${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}KDS Kitchen Display${NC}   →  http://localhost"
echo -e "  ${BOLD}Chatbot / Order Entry${NC} →  http://localhost/static/chat.html"
echo -e "  ${BOLD}Order Form${NC}            →  http://localhost/static/order.html"
echo -e "  ${BOLD}KDS 3-Column Board${NC}    →  http://localhost/static/kds.html"
echo -e "  ${BOLD}KDS Original${NC}          →  http://localhost/static/kds-original.html"
echo -e "  ${BOLD}n8n Automation${NC}        →  http://localhost:${N8N_PORT}  (admin/changeme)"
echo -e "  ${BOLD}Native Ollama API${NC}     →  http://localhost:${OLLAMA_PORT}"
echo ""
echo -e "  ${BOLD}LLM Health${NC}    →  curl http://localhost/api/llm/health"
echo -e "  ${BOLD}RAG Stats${NC}     →  curl http://localhost/api/rag/stats"
echo -e "  ${BOLD}Menu API${NC}      →  curl http://localhost/api/menu"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "  ${CYAN}./start-docker-local-ollama.sh --status${NC}   show container status"
echo -e "  ${CYAN}./start-docker-local-ollama.sh --logs${NC}     tail all logs"
echo -e "  ${CYAN}./start-docker-local-ollama.sh --build${NC}    rebuild kds-app"
echo -e "  ${CYAN}./start-docker-local-ollama.sh --clean${NC}    wipe Docker data and restart"
echo -e "  ${CYAN}./start-docker-local-ollama.sh --stop${NC}     stop Docker services"
echo ""
echo -e "  ${BOLD}Per-service logs:${NC}"
echo -e "  ${CYAN}${COMPOSE_CMD[*]} logs -f kds-app${NC}"
echo -e "  ${CYAN}${COMPOSE_CMD[*]} logs -f n8n${NC}"
echo -e "  ${CYAN}${COMPOSE_CMD[*]} logs -f chromadb${NC}"
echo ""

if ! $all_ok; then
  warn "Some services may still be starting. Run --status in 30s to recheck."
fi
