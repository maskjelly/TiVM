vm:
	colima start --cpu 2 --memory 4 --disk 40
	@colima ssh -- lsblk | grep -E "vda|vdb" || true
	@colima ssh -- df -h / | tail -1

boxes:
	@echo "--- dev boxes ---"
	@docker ps -a --format '{{.Names}} | {{.Status}} | {{.Image}}' 2>/dev/null || echo "(no container runtime running)"
	@echo "--- host ---"
	@colima list 2>/dev/null || echo "(no colima)"
	@du -sh ~/.colima/_lima 2>/dev/null || true

kill:
	docker compose down --remove-orphans >/dev/null 2>&1 || true
	docker container prune -f >/dev/null 2>&1 || true
	docker image prune -f >/dev/null 2>&1 || true
	@echo "dev boxes removed; artifacts in ./runs kept"

nuke: kill
	colima delete -f
	rm -rf ~/.colima/_lima/_disks
	@echo "VM and disks deleted: memory and storage reclaimed (next start: make vm && make up, ~6-8 min)"

status:
	@echo "--- VMs (colima) ---"
	@colima list 2>/dev/null || echo "(no colima)"
	@echo "--- containers ---"
	@docker ps -a --format '{{.Names}} | {{.Status}} | {{.Ports}}' 2>/dev/null || true
	@echo "--- images ---"
	@docker images --format '{{.Repository}}:{{.Tag}} | {{.Size}}' 2>/dev/null || true
	@echo "--- disk ---"
	@docker system df 2>/dev/null || true

up:
	mkdir -p runs
	docker compose down --remove-orphans >/dev/null 2>&1 || true
	docker image prune -f >/dev/null 2>&1 || true
	docker compose up -d --build

down:
	docker compose down

clean:
	docker compose down --remove-orphans >/dev/null 2>&1 || true
	docker image prune -f
	docker builder prune -f
	@echo "artifacts in ./runs are kept; run 'make wipe-runs' to delete them"

wipe-runs:
	rm -rf runs/*

runs:
	@ls -1 runs 2>/dev/null | tail -10 || echo "(no runs yet)"

logs:
	docker compose logs -f desktop

shell:
	docker compose exec desktop bash

check:
	docker compose exec desktop bash -lc 'echo "--- preinstalled stack ---"; for c in apt-get git node npm bun python3 pip3 firefox epiphany tesseract xdotool scrot gcc make; do printf "%-12s %s\n" "$$c" "$$(command -v $$c || echo MISSING)"; done; echo; git --version; node -v; npm -v; bun -v; python3 -V; gcc --version | head -1'

probe:
	docker compose exec desktop python3 -c "import os,requests,json; r=requests.post('https://api.typesafe.ai/v1/systemone', json={'state':'hello','model':'jev-latest','questions':{'ok':{'type':'noul','instructions':'Is this text friendly?'}}}, headers={'Authorization':'Bearer '+os.environ['TYPESAFE_API_KEY']}, timeout=60); print(json.dumps(r.json(), indent=2))"

# --- hosted runner (rove) --------------------------------------------------
ROVE ?= rove

rove-setup:
	ssh $(ROVE) 'bash -s' < deploy/provision-rove.sh

rove-deploy:
	TIVM_HOST=$(ROVE) bash deploy/deploy.sh

rove-up:
	ssh $(ROVE) 'cd /opt/tivm && TIVM_BIND=127.0.0.1 docker compose -f docker-compose.yml -f deploy/docker-compose.rove.yml up -d --build'

rove-down:
	ssh $(ROVE) 'cd /opt/tivm && docker compose -f docker-compose.yml -f deploy/docker-compose.rove.yml down'

rove-logs:
	ssh $(ROVE) 'cd /opt/tivm && docker compose -f docker-compose.yml -f deploy/docker-compose.rove.yml logs -f --tail=100 desktop'

rove-status:
	ssh $(ROVE) 'cd /opt/tivm && docker compose -f docker-compose.yml -f deploy/docker-compose.rove.yml ps && echo && docker system df && echo && swapon --show'

rove-check:
	ssh $(ROVE) "cd /opt/tivm && docker compose -f docker-compose.yml -f deploy/docker-compose.rove.yml exec -T -w /app desktop python3 -c \"import agent.video, agent.report; import shutil; print('modules ok'); print('ffmpeg', shutil.which('ffmpeg') or 'MISSING')\""

tunnel:
	ssh -N -o ExitOnForwardFailure=yes -L 6081:127.0.0.1:6081 -L 6080:127.0.0.1:6080 $(ROVE)

