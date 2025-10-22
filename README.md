# Autoswarm Agent

Enhanced Docker Swarm automation that discovers standalone containers, converts them into services, and keeps Swarm metadata aligned with Dokploy and Traefik.

## Features

- Converts new Docker containers into Swarm services automatically.
- Synchronises `labelsSwarm` and `networkSwarm` data from Dokploy so Traefik rules stay correct.
- Periodic reconciliation loop fixes drifted services and updates Dokploy records.
- Supports multiple domains per application and normalises `Host(...)` rules.

## Architecture

The project has been refactored into a modular, scalable architecture:

```
src/
├── __init__.py           # Package initialization
├── autoswarm.py          # Main entry point and orchestration
├── config.py             # Configuration and constants
├── dokploy_client.py     # Dokploy API client
├── docker_manager.py     # Docker container to Swarm service conversion
├── reconciler.py         # Reconciliation logic (Dokploy ↔ Swarm ↔ Traefik)
├── event_monitor.py      # Docker event monitoring
└── utils.py              # Shared utilities and helpers
```

### Module Responsibilities

- **`config.py`**: Centralized configuration management, environment variables, and logging setup
- **`dokploy_client.py`**: Lightweight API wrapper for Dokploy TRPC endpoints with caching
- **`docker_manager.py`**: Handles conversion of standalone containers to Swarm services
- **`reconciler.py`**: Synchronization logic between Dokploy metadata and Swarm services
- **`event_monitor.py`**: Real-time Docker event monitoring and periodic reconciliation
- **`utils.py`**: Shared utility functions (network resolution, node ID, label checking)
- **`autoswarm.py`**: Main orchestrator that wires all components together

### Benefits of Modular Architecture

✅ **Separation of Concerns**: Each module has a single, well-defined responsibility
✅ **Testability**: Individual components can be tested in isolation
✅ **Maintainability**: Easier to locate and fix issues
✅ **Scalability**: Components can be extended or replaced independently
✅ **Readability**: Smaller, focused modules are easier to understand

## Usage

1. Provide the following environment variables:

   ```bash
   AUTOSWARM_DOKPLOY_URL=http://dokploy:3000
   AUTOSWARM_DOKPLOY_API_KEY=<dokploy-api-key>
   AUTOSWARM_TRAEFIK_NETWORK=traefik-public
   AUTOSWARM_RECONCILE_INTERVAL=60
   ```

2. Mount the Docker socket: `-v /var/run/docker.sock:/var/run/docker.sock`.
3. (Optional) Attach overlay networks used in your Swarm stack.

Run with Docker:

```bash
docker run -d \
  -e AUTOSWARM_DOKPLOY_URL=http://dokploy:3000 \
  -e AUTOSWARM_DOKPLOY_API_KEY=$DOKPLOY_API_KEY \
  -e AUTOSWARM_TRAEFIK_NETWORK=traefik-public \
  -e AUTOSWARM_RECONCILE_INTERVAL=60 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  ghcr.io/nexgensystemsmx/autoswarm-agent:latest
```

## Development

```bash
pip install -r requirements.txt
python src/autoswarm.py
```

The script logs detailed reconciliation updates and will exit cleanly on SIGINT/SIGTERM.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOSWARM_DOKPLOY_URL` | `http://dokploy:3000` | Dokploy API base URL |
| `AUTOSWARM_DOKPLOY_API_KEY` | - | API key for Dokploy authentication |
| `AUTOSWARM_TRAEFIK_NETWORK` | `traefik-public` | Name of the Traefik overlay network |
| `AUTOSWARM_RECONCILE_INTERVAL` | `60` | Reconciliation interval in seconds |
| `AUTOSWARM_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `AUTOSWARM_DOKPLOY_CACHE_TTL` | `30` | Dokploy cache TTL in seconds |
| `DOCKER_HOST` | `unix://var/run/docker.sock` | Docker daemon socket |

## How It Works

### 1. Initial Sweep
On startup, the agent scans all existing containers and converts unmanaged ones into Swarm services.

### 2. Event Monitoring
Continuously monitors Docker events for new container creation/start events and automatically converts them to services.

### 3. Service Reconciliation
Periodically (default: 60s) reconciles Swarm services with Dokploy metadata:
- Updates service labels to match Dokploy configuration
- Ensures correct network attachments (including Traefik network)
- Normalizes Traefik router rules based on domain configuration
- Updates Dokploy records when discrepancies are detected

### 4. Graceful Shutdown
Handles SIGINT/SIGTERM signals for clean shutdown of all monitoring threads.

## Labels

- `autoswarm.ignore=true`: Skip conversion of a container to a Swarm service
- `autoswarm.managed=true`: Indicates a service is managed by Autoswarm

## License

MIT License
