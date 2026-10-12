#!/usr/bin/env bash
# Runs the API, the replay worker and the payment process in one container, for hosts
# where each extra service costs money. If any of the three stops, the container stops,
# so the host restarts all of them together.
set -euo pipefail

# Bring the database up to date before anything reads it. A failure stops the start.
alembic upgrade head

python -m agentbounty.workers.replay &
python -m agentbounty.workers.payment &
uvicorn agentbounty.app:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers &

wait -n
exit 1
