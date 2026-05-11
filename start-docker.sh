#!/usr/bin/env bash
# =============================================================================
# KDS-AI  —  start-docker.sh
# Starts the full production stack via Docker Compose
#
# Services:
#   kds-app   — Flask / gunicorn (port 8000 internal, 80 via nginx)
#   ollama    — LLM server (llama3.1 + mistral + nomic-embed-text)
#   chromadb  — RAG vector store
#   nginx     — Reverse proxy + WebSocket (port 80)
#   n8n       — Workflow automation (port 5678)
#
# Usage:
#   chmod +x start-docker.sh
#   ./start-docker.sh           # full stack
#   ./start-docker.sh --build   # rebuild kds-app image before starting
#   ./start-docker.sh --clean   # wipe volumes and rebuild from scratch
#   ./start-docker.sh --stop    # stop all services
#   ./start-docker.sh --logs    # tail all logs
#   ./start-docker.sh --status  # show container status
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
COMPOSE_FILE="docker-compose.yml"
MODELS=("llama3.1" "mistral" "nomic-embed-text")
APP_PORT=80
N8N_PORT=5678
OLLAMA_PORT=11434
N8N_WORKFLOW_FILE="/home/node/.n8n/workflows/kds_workflow.json"

# ── Flags ─────────────────────────────────────────────────────────────────────
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

# ── Helpers ───────────────────────────────────────────────────────────────────
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
  docker compose exec -u node n8n n8n import:workflow --input="$N8N_WORKFLOW_FILE"

  log "Activating imported workflow..."
  docker compose exec -u node n8n n8n update:workflow --all --active=true

  log "Restarting n8n so imported workflow activation takes effect..."
  docker compose restart n8n
  sleep 3

  success "n8n workflow imported from n8n/kds_workflow.json"
}

wait_healthy() {
  local service=$1
  local max=60
  local count=0
  log "Waiting for $service to be healthy..."
  while true; do
    status=$(docker compose ps --format json "$service" 2>/dev/null \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Health',''))" 2>/dev/null || echo "")
    if [ "$status" = "healthy" ]; then
      success "$service is healthy"
      return 0
    fi
    sleep 2
    count=$((count + 2))
    if [ $count -ge $max ]; then
      warn "$service health check timed out — continuing anyway"
      return 0
    fi
    echo -ne "  waiting ${count}s...\r"
  done
}

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
echo -e "  ${BOLD}Kitchen Display System — Docker Stack${NC}"
echo -e "  nginx · Flask · Ollama · ChromaDB · n8n"
echo ""

# ── Handle simple commands first ──────────────────────────────────────────────
if $STOP; then
  header "Stopping KDS-AI Docker Stack"
  docker compose down
  success "All services stopped"
  exit 0
fi

if $SHOW_LOGS; then
  docker compose logs -f
  exit 0
fi

if $STATUS; then
  header "KDS-AI Service Status"
  docker compose ps
  exit 0
fi

# ── Pre-flight checks ─────────────────────────────────────────────────────────
header "Pre-flight checks"

# Docker running?
if ! docker info &>/dev/null; then
  error "Docker daemon is not running. Start Docker Desktop first.\n    → open -a Docker"
fi
success "Docker daemon running"

# docker compose available?
if ! docker compose version &>/dev/null; then
  error "docker compose not found. Update Docker Desktop to v2.x+"
fi
success "docker compose available"

# .env exists?
if [ ! -f ".env" ]; then
  warn ".env not found — copying from .env.template"
  if [ -f ".env.template" ]; then
    cp .env.template .env
    warn "Edit .env with your STRIPE_SECRET_KEY before continuing."
    warn "Then re-run this script."
    exit 1
  else
    error ".env.template not found. Create .env manually."
  fi
fi
success ".env found"

# docker-compose.yml exists?
if [ ! -f "$COMPOSE_FILE" ]; then
  error "$COMPOSE_FILE not found. Run from repo root."
fi
success "$COMPOSE_FILE found"

# ── Clean mode ────────────────────────────────────────────────────────────────
if $CLEAN; then
  header "Clean mode — wiping volumes and images"
  warn "This will delete all ChromaDB data, n8n workflows, and Ollama models."
  read -r -p "  Are you sure? (y/N) " confirm
  if [[ "$confirm" =~ ^[Yy]$ ]]; then
    docker compose down -v --remove-orphans
    rm -rf ./chroma_db/
    success "Volumes and local chroma_db wiped"
    BUILD=true
  else
    log "Clean cancelled"
    exit 0
  fi
fi

# ── Build ─────────────────────────────────────────────────────────────────────
if $BUILD; then
  header "Building kds-app image"
  docker compose build kds-app
  success "kds-app image built"
fi

# ── Start core services ───────────────────────────────────────────────────────
header "Starting core services"

log "Bringing up: chromadb, n8n..."
docker compose up -d chromadb n8n
success "chromadb and n8n started"

if $CLEAN; then
  seed_n8n_workflow
fi

log "Bringing up: ollama..."
docker compose up -d ollama
sleep 3   # give ollama a moment before health check

# ── Pull Ollama models ────────────────────────────────────────────────────────
header "Checking Ollama models"

# Wait for ollama to be reachable
max_wait=120
waited=0
while ! docker compose exec ollama ollama list &>/dev/null; do
  sleep 3
  waited=$((waited + 3))
  echo -ne "  waiting for Ollama ${waited}s...\r"
  if [ $waited -ge $max_wait ]; then
    warn "Ollama taking long to start — check: docker compose logs ollama"
    break
  fi
done
echo ""

for model in "${MODELS[@]}"; do
  if docker compose exec ollama ollama list 2>/dev/null | grep -q "^${model}"; then
    success "Model ready: $model"
  else
    log "Pulling model: $model (may take several minutes)..."
    docker compose exec ollama ollama pull "$model"
    success "Pulled: $model"
  fi
done

# ── Start app + nginx ─────────────────────────────────────────────────────────
header "Starting kds-app and nginx"

docker compose up -d kds-app nginx
sleep 3

# ── Verify all containers ─────────────────────────────────────────────────────
header "Verifying services"

services=("kds-app" "ollama" "chromadb" "nginx" "n8n")
all_ok=true

for svc in "${services[@]}"; do
  state=$(docker compose ps --format json "$svc" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('State','unknown'))" 2>/dev/null || echo "unknown")
  if [ "$state" = "running" ]; then
    success "$svc — running"
  else
    warn "$svc — $state (check: docker compose logs $svc)"
    all_ok=false
  fi
done

# ── LLM health check ──────────────────────────────────────────────────────────
header "LLM health check"
sleep 2
response=$(curl -s --max-time 10 http://localhost/api/llm/health 2>/dev/null || echo "")
if echo "$response" | grep -q '"status":"ok"'; then
  success "LLM orchestrator online — both models responding"
elif echo "$response" | grep -q "unavailable"; then
  warn "LLM orchestrator initializing — RAG indexing may still be running"
  warn "Check: docker compose logs -f kds-app"
else
  warn "LLM health endpoint unreachable — nginx may still be starting"
fi

# ── Ready ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  KDS-AI Docker Stack is LIVE${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}KDS Kitchen Display${NC}   →  http://localhost"
echo -e "  ${BOLD}Chatbot / Order Entry${NC} →  http://localhost/static/chat.html"
echo -e "  ${BOLD}Order Form${NC}            →  http://localhost/static/order.html"
echo -e "  ${BOLD}KDS 3-Column Board${NC}    →  http://localhost/static/kds.html"
echo -e "  ${BOLD}n8n Automation${NC}        →  http://localhost:${N8N_PORT}  (admin/changeme)"
echo -e "  ${BOLD}Ollama API${NC}            →  http://localhost:${OLLAMA_PORT}"
echo ""
echo -e "  ${BOLD}LLM Health${NC}    →  curl http://localhost/api/llm/health"
echo -e "  ${BOLD}RAG Stats${NC}     →  curl http://localhost/api/rag/stats"
echo -e "  ${BOLD}Menu API${NC}      →  curl http://localhost/api/menu"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "  ${CYAN}./start-docker.sh --status${NC}   show container status"
echo -e "  ${CYAN}./start-docker.sh --logs${NC}     tail all logs"
echo -e "  ${CYAN}./start-docker.sh --build${NC}    rebuild kds-app"
echo -e "  ${CYAN}./start-docker.sh --clean${NC}    wipe everything and restart"
echo -e "  ${CYAN}./start-docker.sh --stop${NC}     stop all services"
echo ""
echo -e "  ${BOLD}Per-service logs:${NC}"
echo -e "  ${CYAN}docker compose logs -f kds-app${NC}"
echo -e "  ${CYAN}docker compose logs -f ollama${NC}"
echo -e "  ${CYAN}docker compose logs -f n8n${NC}"
echo ""

if ! $all_ok; then
  warn "Some services may still be starting. Run --status in 30s to recheck."
fi
