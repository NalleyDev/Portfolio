# Sanitization Notes

Every hardcoded credential, real hostname, and internal identifier has been replaced with a placeholder. This file lists what changed and where you set the real values in your own environment.

**Do this first:** rotate the GlobalNOC service account password. It was committed in plaintext and is in the git history, so scrubbing the file alone is not enough.

---

## Critical — exposed secrets

### `Python/testgrnoc_api.py`

| Was | Now |
|---|---|
| `GN_PW = '<plaintext password>'` | `os.getenv('GN_PW', '')` |
| `GN_HOST = '<internal CDS host>'` | `https://cds.example.com/cds2/` |
| `GN_USER = '<service account>'` | `reporting-service@EXAMPLE.COM` |
| `GN_REALM = '<internal IdP host>'` | `https://idp.example.com/...` |
| `NODE_TYPES = ['48']` | read from env |

The script now exits with an error if `GN_PW` is unset rather than failing silently.

**Where you set the real values:** export them in your shell, or source `Python/.env.example` after copying it to `.env`.

```bash
export GN_HOST="https://your-real-cds-host/cds2/"
export GN_USER="your-service-account@YOUR.REALM"
export GN_PW="..."
export GN_REALM="https://your-idp/idp/profile/SAML2/SOAP/ECP"
export NODE_TYPES="48"
```

### `Docker/SemaphoreDC/docker-compose.yml`

| Was | Now |
|---|---|
| `SEMAPHORE_ACCESS_KEY_ENCRYPTION: <base64 key>` | `CHANGE_ME_BASE64_32_BYTE_KEY` |
| `dc=local,dc=<internal domain>,dc=net` | `dc=example,dc=com` |
| `MYSQL_PASSWORD: semaphore` / `SEMAPHORE_DB_PASS: semaphore` | `CHANGE_ME_DB_PASSWORD` (must match each other) |
| `SEMAPHORE_ADMIN_PASSWORD: changeme` | `CHANGE_ME_ADMIN_PASSWORD` |
| `SEMAPHORE_LDAP_PASSWORD: 'ldap_bind_account_password'` | `CHANGE_ME_LDAP_BIND_PASSWORD` |
| `dc01.local.example.com` | `dc01.example.com` |
| `admin@localhost` | `admin@example.com` |

That encryption key was a working key — anything encrypted with it in a live Semaphore instance should be re-keyed.

**Where you set the real values:** edit the `environment:` block, or better, move them to a `.env` file next to the compose file and reference them as `${SEMAPHORE_DB_PASS}`. Generate a new encryption key with:

```bash
head -c32 /dev/urandom | base64
```

---

## Placeholders and internal identifiers

### `Python/alan_dashboard_v3.py`

Already env-driven, but two defaults leaked internal detail:

| Was | Now |
|---|---|
| `NODE_TAG_TYPE` default `'<internal tag name>'` | `'CHANGE_ME_TAG_NAME'` |
| `NODE_TYPES` default `'48,86,4,20,87'` | empty (must be set) |

**Where you set the real values:** `Python/.env.example` → copy to `.env`, fill in, and load it before running.

Two things worth considering separately:

- The filename itself (`alan_dashboard_v3.py`) reads like an internal project name. Renaming to `patch_dashboard.py` would finish the job.
- SSH passwords are stored in the Flask session and passed to `sudo` over stdin. That's fine on a trusted network but is the weakest part of the app if it's ever exposed more widely — SSH keys would be the upgrade.

### `Docker/PortainerDC/nginx.conf`

`server_name <FQDN of server>;` and `ssl_certificate /etc/nginx/certs/<example.crt>;` used angle brackets, which nginx rejects — the config would not have loaded as written. Replaced with `portainer.example.com` and unbracketed filenames, matching the style already used in `ManyfoldDC/nginx.conf`.

**Where you set the real values:** both `server_name` lines, and the two `ssl_certificate*` paths if your cert files are named differently.

### `Docker/PortainerDC/certs/`

`example.crt` and `example.key` contain only comment text, not real key material. Left as-is. The `.gitignore` now blocks `*.crt`, `*.key`, and `*.pem` with an exception carved out for these two placeholders, so a real cert can't be committed by accident.

---

## Terraform

### `Terraform/k8s Provisioning/`

Two problems beyond sanitization:

1. `main.tf` declared `variable "vsphere_user"`, `variable "datacenter"`, and eleven others that are **also** declared in `variables.tf`. Terraform rejects duplicate declarations in the same directory, so this never ran. The duplicates in `main.tf` have been removed; `variables.tf` is now the single source.
2. IPs were hardcoded (`192.168.1.${50 + count.index}`, gateway `192.168.1.1`). Now driven by four new variables: `node_network_prefix`, `node_gateway`, `control_plane_ip_start`, `worker_ip_start`.

`terraform.tfvars` has been renamed to `terraform.tfvars.example`.

**Where you set the real values:**

```bash
cd "Terraform/k8s Provisioning"
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars — vsphere_user, vsphere_password, vsphere_server,
# datacenter, datastore, network, cluster, node_network_prefix, node_gateway
```

`terraform.tfvars` is gitignored; `terraform.tfvars.example` is not.

### `Terraform/pan-os Provisioning/`

This directory had **no** variable declarations at all — `main.tf` referenced `var.vsphere_user`, `var.panos_host`, `var.domain`, and ten others that were never defined, so `terraform plan` would have failed immediately. Added:

- `variables.tf` with all sixteen variables, `sensitive = true` on both password variables
- `terraform.tfvars.example`

Hardcoded values moved into variables: `fw_ip_address` (was `192.168.2.1`), `fw_gateway` (was `192.168.2.254`), and `trust_cidr` (was `192.168.2.0/24`, used twice in the security rule).

**Where you set the real values:**

```bash
cd "Terraform/pan-os Provisioning"
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars — vsphere_*, panos_host, panos_user, panos_password
```

---

## Left alone deliberately

- **`Ansible/SRX_Code_Upgrade.survey.json`** — the default image URL already uses `juniper.example.net`, which is a reserved documentation domain. Fine as-is.
- **`Ansible/SRX_Code_Upgrade.yml`** — no hardcoded hosts or credentials; everything comes from inventory and survey variables.
- **`K8s/*.yml`** — `admin_user: admin` is the AWX default, and the password is read from a generated secret. Nothing to scrub.
- **`Docker/ManyfoldDC/`** — already used `example.com`, `changeme`, and `replace_with_long_random_hex` throughout. No changes needed.
- **`Docker/URbackupDC/`** — `/mnt/urbackup` is a generic path. Fine.
- **`Bash/`, `Python/Pokemon.py`, `Python/robot battle/`, context manager exercises** — no sensitive content.
- **RFC 1918 addresses in defaults** — `192.168.x.x` ranges are non-routable and reveal nothing, so they remain as variable defaults rather than being stripped.

---

## New files

| File | Purpose |
|---|---|
| `.gitignore` | Blocks `*.tfvars`, `.env`, `users.json`, `*.db`, and TLS material |
| `Python/.env.example` | Every environment variable the dashboard and API script read |
| `Terraform/k8s Provisioning/terraform.tfvars.example` | Replaces the committed `terraform.tfvars` |
| `Terraform/pan-os Provisioning/variables.tf` | Was missing entirely |
| `Terraform/pan-os Provisioning/terraform.tfvars.example` | Was missing entirely |

---

## Scrubbing the git history

Replacing the file does not remove the password from earlier commits. After rotating the credential:

```bash
pip install git-filter-repo

cd /path/to/Portfolio
git filter-repo --replace-text <(echo 'THE_OLD_PASSWORD_HERE==>REDACTED')

git push --force origin main
```

`git filter-repo` refuses to run on a repo with existing remotes unless you pass `--force`, and it rewrites every commit SHA — if anyone has cloned this, they will need a fresh clone. Given the repo has no forks and no other contributors, that's not a concern here.

Note: fill in the literal old password in that `filter-repo` command from your own records — it is deliberately not written down in this file, so this file is safe to commit.

Also revoke and reissue anything else that account had access to, on the assumption that a public repo with a plaintext password was scraped.
