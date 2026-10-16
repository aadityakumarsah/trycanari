#!/usr/bin/env bash
# Runs the API, the replay worker and the payment process in one container, for hosts
# where each extra service costs money. If any of the three stops, the container stops,
# so the host restarts all of them together.
set -euo pipefail

# Bring the database up to date before anything reads it. A failure stops the start.
alembic upgrade head

pids=()

stop_children() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

trap stop_children EXIT INT TERM

python -m agentbounty.workers.replay & pids+=("$!")
python -m agentbounty.workers.payment & pids+=("$!")
uvicorn agentbounty.app:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers & pids+=("$!")

# Any dead child makes the host restart the whole coordinated service. The trap first
# stops its siblings, preventing duplicate workers from surviving during replacement.
wait -n "${pids[@]}" || true
exit 1
