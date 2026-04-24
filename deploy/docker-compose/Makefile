.PHONY: help up down restart logs ps verify pull backup demo-up nuke

DC := docker compose --env-file .env -f compose/docker-compose.yml -p pcap

help: ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "PCAP docker-compose stack\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

up: ## bring the full stack up (default profile)
	@bash scripts/setup.sh

demo-up: ## up + synthetic metric feeder
	@bash scripts/setup.sh demo

down: ## stop services, keep volumes
	$(DC) down

nuke: ## stop + drop volumes (destructive)
	$(DC) down -v

restart: down up ## recreate everything

logs: ## tail all service logs
	$(DC) logs -f

ps: ## service status
	$(DC) ps

verify: ## curl healthchecks end-to-end
	@echo "PCAP:    " && curl -fsS http://localhost:$${PCAP_API_PORT:-8090}/healthz | head -c 200 && echo
	@echo "Grafana: " && curl -fsS http://localhost:$${GRAFANA_PORT:-3000}/api/health | head -c 200 && echo
	@echo "Mimir:   " && curl -fsS http://localhost:$${MIMIR_PORT:-9009}/ready | head -c 200 && echo

pull: ## pre-pull all images (for offline / air-gapped registries)
	@bash scripts/pull-images.sh

backup: ## snapshot the audit DB + Redis
	@bash scripts/backup.sh
