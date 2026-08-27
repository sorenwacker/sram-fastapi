# Deployment

This guide covers deploying the SRAM FastAPI application.

## Quick Start

Use the Makefile for common operations:

```bash
make deploy        # Deploy to production
make deploy-check  # Dry-run (no changes)
make status        # Check service status
make logs          # View server logs
make restart       # Restart service
```

## Prerequisites

- Ansible 2.9+
- SSH access to the target server
- `sudo` privileges on the target server
- SRAM OIDC credentials (see [SRAM Setup](sram-setup.md))

## Configuration

### 1. Set Up Vault Password

Create a vault password file (one-time setup):

```bash
echo "your-vault-password" > ~/.vault_pass
chmod 600 ~/.vault_pass
```

### 2. Configure Secrets

Encrypt the vault file:

```bash
cd ansible
ansible-vault encrypt group_vars/sram_demo/vault.yml
```

Edit secrets:

```bash
ansible-vault edit group_vars/sram_demo/vault.yml
```

Required secrets:

| Variable | Description |
|----------|-------------|
| `vault_sram_oidc_client_id` | OIDC client ID from SRAM |
| `vault_sram_oidc_client_secret` | OIDC client secret from SRAM |
| `vault_secret_key` | Session encryption key |

Generate a secret key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Configure Variables

Edit `ansible/group_vars/sram_demo/vars.yml` for non-secret configuration:

| Variable | Description | Default |
|----------|-------------|---------|
| `app_name` | Application display name | SRAM Demo |
| `app_dir` | Installation directory | /app/sram-fastapi |
| `base_url` | Public URL | https://sram-demo.ewi.tudelft.nl |
| `gunicorn_workers` | Number of worker processes | 4 |
| `gunicorn_port` | Port to bind | 8080 |

## Deployment

### Dry Run

Test the deployment without making changes:

```bash
make deploy-check
```

### Deploy

Run the deployment:

```bash
make deploy
```

### Verify

Check the service status:

```bash
make status
```

Check logs:

```bash
make logs
```

## Continuous integration

Two workflows run in GitHub Actions:

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `docs.yml` | Push to main | Build and publish the documentation site |
| `codeql.yml` | Pull request to main, and weekly on Monday 06:00 UTC | CodeQL static analysis of the Python sources, reported under the repository's Security tab |

CodeQL runs the `security-and-quality` query suite. It is not run on every push: pull requests and the weekly schedule cover the same code without spending CI minutes on intermediate commits.

LGTM.com, which previously offered this analysis as an external service, was retired in December 2022; its analysis is now part of GitHub code scanning.

## Ansible Directory Structure

```
ansible/
  ansible.cfg              # Ansible configuration
  deploy.yml               # Main playbook
  inventory/
    hosts.yml              # Server inventory
  group_vars/
    sram_demo/
      vars.yml             # Non-secret variables
      vault.yml            # Vault-encrypted secrets
  templates/
    env.j2                 # .env template
    sram-demo.service.j2   # systemd service template
```

## Manual Deployment

If not using Ansible, deploy manually:

```bash
# Sync files
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    . user@server:/app/sram-fastapi/

# Install dependencies
ssh user@server "cd /app/sram-fastapi && uv sync --no-dev"

# Create .env file with your configuration
ssh user@server "nano /app/sram-fastapi/.env"

# Install and start service
ssh user@server "sudo cp sram-demo.service /etc/systemd/system/"
ssh user@server "sudo systemctl daemon-reload"
ssh user@server "sudo systemctl enable --now sram-demo"
```

## Nginx Configuration

Example nginx configuration for reverse proxy:

```nginx
server {
    listen 443 ssl http2;
    server_name sram-demo.ewi.tudelft.nl;

    ssl_certificate /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for large session cookies with OIDC claims
        proxy_buffer_size 128k;
        proxy_buffers 4 256k;
        proxy_busy_buffers_size 256k;
    }
}
```

The proxy buffer settings are required because SRAM OIDC responses include user claims (entitlements, affiliations) that can result in large session cookies.
