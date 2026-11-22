COMPOSE = docker compose --env-file docker/.env -f docker/docker-compose.yaml

.PHONY: dev build compare env stop

dev: build
	@echo "starting development services..."
	@$(COMPOSE) -p workflow up -d --force-recreate --remove-orphans

build: env
	@docker/build-web.sh
	@$(COMPOSE) -p workflow build

compare: build
	@$(COMPOSE) --profile compare -p workflow up -d --build web-legacy
	@$(COMPOSE) --profile compare -p workflow up -d --force-recreate nginx

env:
	@test -f docker/.env || { \
		cp docker/.env.example docker/.env; \
		python3 -c "from pathlib import Path; import secrets; p=Path('docker/.env'); lines=p.read_text().splitlines(); keys={'SECRET_KEY','DB_PASSWORD','REDIS_PASSWORD','FILE_SIGNING_KEY','OPENDAL_S3_ACCESS_KEY_ID','OPENDAL_S3_SECRET_ACCESS_KEY','SANDBOX_API_KEY'}; p.write_text('\n'.join((line.split('=')[0]+'='+secrets.token_hex(32)) if line.split('=')[0] in keys else line for line in lines)+'\n')"; \
	}

stop:
	@$(COMPOSE) --profile compare -p workflow stop
