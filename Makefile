up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f desktop

shell:
	docker compose exec desktop bash

check:
	docker compose exec desktop bash -lc 'echo "--- preinstalled stack ---"; for c in apt-get git node npm bun python3 pip3 firefox epiphany tesseract xdotool scrot gcc make; do printf "%-12s %s\n" "$$c" "$$(command -v $$c || echo MISSING)"; done; echo; git --version; node -v; npm -v; bun -v; python3 -V; gcc --version | head -1'

probe:
	docker compose exec desktop python3 -c "import os,requests,json; r=requests.post('https://api.typesafe.ai/v1/systemone', json={'state':'hello','model':'jev-latest','questions':{'ok':{'type':'noul','instructions':'Is this text friendly?'}}}, headers={'Authorization':'Bearer '+os.environ['TYPESAFE_API_KEY']}, timeout=60); print(json.dumps(r.json(), indent=2))"
