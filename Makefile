.PHONY: build build-base build-sim build-gzweb up down sim logs clean

# Build everything (base must come first — others depend on it)
build: build-base
	docker compose build sim gzweb gs agent1 agent2 agent3 viz

build-base:
	docker compose build base

build-sim: build-base
	docker compose build sim

build-gzweb: build-base
	docker compose build gzweb

# Start the full stack
up:
	docker compose up

# Start only the quadrotor simulation + web viewer
sim:
	docker compose up sim gzweb

down:
	docker compose down

logs:
	docker compose logs -f

# Remove stopped containers and dangling images
clean:
	docker compose down --remove-orphans
	docker image prune -f
