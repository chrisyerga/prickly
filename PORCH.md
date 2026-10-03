# Porch Deployment

This service is managed by Porch.

- Service id: `prickly`
- Domain: `prickly.newtricks.ai`
- Container: `prickly-web`
- Internal port: `8000`
- Host: `milo.newtricks.ai` (deploy path `/opt/prickly`)

Porch has no Python scaffold, so the `Dockerfile` and workflows here are hand-written to the same shape as the Node services. Host routing, DNS, TLS, and Caddy reloads are owned by `npx @lindale/porch service register --json` on the VPS.

## Workflows

- `.github/workflows/ci.yml`: ruff, pyright, pytest (including the real-model tests, with `~/.cache/cactus-needle` cached), and a Docker build on PRs and non-main pushes. Also called by the deploy workflow.
- `.github/workflows/deploy.yml`: on push to `main`, runs CI, pushes `ghcr.io/chrisyerga/prickly:<sha>` for `linux/amd64`, then registers the service on the host over SSH.

## Repository secrets

| Secret | Value |
| --- | --- |
| `PORCH_HOST` | `milo.newtricks.ai` |
| `PORCH_USER` | `root`, since `/etc/porch` and `/opt` are root-owned on milo |
| `PORCH_SSH_KEY` | Private key whose public half is in the host user's `authorized_keys` |
| `DIGITALOCEAN_TOKEN` | DigitalOcean API token with write access to the `newtricks.ai` domain; Porch upserts the `prickly` A record with it |

The app itself needs no secrets.

## Image and runtime notes

- The Docker build downloads the Cactus engine (`libneedle.so`) and both weight files (`needle3.cact`, `whistle.cact`) and runs a full model warm-up, so a broken engine fails the build instead of the deploy. The runtime image sets `HF_HUB_OFFLINE=1` and never fetches at startup.
- One uvicorn worker: the native engine holds one model set per process and is not thread-safe, and milo has 1 vCPU and about 350 MB free. The engine peaks around 170-200 MB RSS.
- `NEEDLE_TELEMETRY=0` is set in the image to turn off cactus-needle's anonymous usage pings.
- Host notes from jev-ui apply: Node on milo is installed with nvm, so the deploy script sources `~/.nvm/nvm.sh`; the GHCR package must be public (or the host logged in to `ghcr.io`).
