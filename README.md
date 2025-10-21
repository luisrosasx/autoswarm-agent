# Autoswarm Agent

Enhanced Docker Swarm automation that discovers standalone containers, converts them into services, and keeps Swarm metadata aligned with Dokploy and Traefik.

## Features

- Converts new Docker containers into Swarm services automatically.
- Synchronises `labelsSwarm` and `networkSwarm` data from Dokploy so Traefik rules stay correct.
- Periodic reconciliation loop fixes drifted services and updates Dokploy records.
- Supports multiple domains per application and normalises `Host(...)` rules.

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

## License

MIT License
