up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f desktop

shell:
	docker compose exec desktop bash

probe:
	docker compose exec desktop python3 -c "import os,requests,json; r=requests.post('https://api.typesafe.ai/v1/systemone', json={'state':'hello','model':'jev-latest','questions':{'ok':{'type':'noul','instructions':'Is this text friendly?'}}}, headers={'Authorization':'Bearer '+os.environ['TYPESAFE_API_KEY']}, timeout=60); print(json.dumps(r.json(), indent=2))"
