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
	@echo "VM deleted: memory and storage reclaimed (next start: make vm && make up, ~6-8 min)"

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
