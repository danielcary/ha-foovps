#!/usr/bin/env bash
# Full Supervisor environment, isolated from the host's Docker containers.
set -euo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
container=myvps-ha-dev

case "${1:-status}" in
  start)
    if docker container inspect "$container" >/dev/null 2>&1; then
      docker start "$container" >/dev/null
    else
      docker run -d --name "$container" --privileged --cgroupns=host \
        -e SUPERVISOR_UNCONFINED=1 -e SUPERVISOR_CHANNEL=stable \
        -e WORKSPACE_DIRECTORY=/mnt/supervisor/apps/local/ha-myvps \
        -p 127.0.0.1:7123:80 -p 127.0.0.1:7357:4357 \
        -p 127.0.0.1:8000:8000 -p 127.0.0.1:8443:8443 -p 127.0.0.1:2222:2222 \
        --mount type=volume,src=myvps-ha-docker,dst=/var/lib/docker \
        --mount type=volume,src=myvps-ha-containerd,dst=/var/lib/containerd \
        --mount type=volume,src=myvps-ha-supervisor,dst=/mnt/supervisor \
        --mount "type=bind,src=$task_root,dst=/mnt/supervisor/apps/local/ha-myvps" \
        --tmpfs /tmp --tmpfs /run ghcr.io/home-assistant/devcontainer:5-apps
    fi
    for attempt in $(seq 1 45); do
      if docker exec "$container" docker info >/dev/null 2>&1; then break; fi
      sleep 2
    done
    if ! docker exec "$container" docker inspect hassio_supervisor >/dev/null 2>&1; then
      docker exec -d "$container" bash -lc 'supervisor_run > /tmp/myvps-supervisor.log 2>&1'
    fi
    echo 'Home Assistant is starting at http://localhost:7123/'
    ;;
  install)
    docker exec "$container" ha store reload
    docker exec "$container" ha apps install local_myvps
    docker exec "$container" ha apps start local_myvps
    ;;
  rebuild)
    docker exec "$container" ha apps rebuild --force local_myvps
    docker exec "$container" ha apps start local_myvps
    ;;
  status)
    docker exec "$container" ha supervisor info
    docker exec "$container" ha apps info local_myvps
    ;;
  logs)
    docker exec "$container" ha apps logs local_myvps
    ;;
  stop)
    docker stop --time 60 "$container"
    ;;
  *) echo 'Usage: scripts/ha_dev.sh start|install|rebuild|status|logs|stop' >&2; exit 2 ;;
esac
