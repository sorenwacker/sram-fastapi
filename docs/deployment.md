# Deployment

This guide covers deploying the SRAM FastAPI application using Ansible.

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
cd ansible
ansible-playbook deploy.yml --check
```

### Deploy

Run the deployment:

```bash
ansible-playbook deploy.yml
```

### Verify

Check the service status:

```bash
ansible sram_demo -a "systemctl status sram-demo" --become
```

Check logs:

```bash
ansible sram_demo -a "journalctl -u sram-demo -n 50 --no-pager" --become
```

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
    }
}
```
