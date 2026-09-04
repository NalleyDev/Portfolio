# Portfolio

A collection of infrastructure automation and scripting work — Ansible playbooks, Terraform configs, Kubernetes manifests, Docker Compose stacks, Bash utilities, and Python projects. Most of it comes out of real network and systems work: Junos upgrades, AWX deployments, vSphere provisioning, and patch management.

Everything here is self-contained. Pick a directory and go.

## Contents

| Directory | What's in it |
|---|---|
| [`Ansible/`](Ansible) | Junos SRX code upgrades and Linux patching playbooks |
| [`Bash/`](Bash) | Build and media conversion scripts |
| [`Docker/`](Docker) | Compose stacks for self-hosted services |
| [`K8s/`](K8s) | AWX deployment manifests |
| [`Python/`](Python) | A Flask patch-management dashboard plus smaller exercises |
| [`Terraform/`](Terraform) | vSphere and PAN-OS provisioning |

Every credential, hostname, and network address in this repo is a placeholder. Copy the `.example` files, fill in your own values, and keep the filled-in versions out of version control — `.gitignore` already covers `*.tfvars`, `.env`, `users.json`, `*.db`, and TLS material.

---

## Ansible

**`SRX_Code_Upgrade.yml`** — A full Junos upgrade workflow for SRX firewalls, built to run from AWX. It reads the device model and current version over NETCONF, picks the right image (either a single URL or a per-model map for mixed inventories), runs `request system storage cleanup` and `consolidate`, gates the install on available free space, stages the package, installs, reboots, waits for NETCONF to come back, and verifies the device is actually running the target version before reporting success.

Notable behavior:

- Skips devices already on the target version
- `check_only` mode runs the storage checks and reports readiness without installing anything
- Batch size, timeouts, staging directories, and TLS verification are all parameterized
- Confirms the device genuinely went down before waiting for it to come back, so a failed reboot doesn't silently pass

Requires the `juniper.device` collection.

**`SRX_Code_Upgrade.survey.json`** — The matching AWX survey. Import it into the job template to get prompts for image URL, per-model map, target version override, pre-check mode, and the various timeouts.

**`update.yml`** — Updates and autoremoves packages across mixed Linux fleets, branching on `ansible_os_family` for `apt` and `dnf`.

## Bash

**`convert_to_h265.sh`** — Batch re-encodes a directory of video files to H.265 via HandBrakeCLI, running up to four jobs in parallel. Writes to a temp file first and only replaces the original on a successful exit, so a failed encode doesn't cost you the source.

```bash
./convert_to_h265.sh /path/to/season_folder
```

**`build.sh`** — A small interactive build script: reads the version from the first line of `source/changelog.md`, confirms with the user, then copies everything except `secretinfo.md` into `build/`.

## Docker

Compose stacks for self-hosted infrastructure. Each directory stands alone — `cd` into it and `docker compose up -d`.

- **`PortainerDC/`** — Portainer CE behind an nginx reverse proxy with TLS. Set `server_name` in `nginx.conf` and drop real certs into `certs/` (the two files there are placeholders, not key material).
- **`SemaphoreDC/`** — Semaphore UI with MySQL. A Postgres alternative is included commented out, and LDAP is wired up but switched off via `SEMAPHORE_LDAP_ACTIVATED: 'no'`.
- **`URbackupDC/`** — UrBackup server. Expects an NFS share mounted at `/mnt/urbackup` on the host.
- **`ManyfoldDC/`** — Manyfold with Postgres, Redis, and nginx. See the [directory README](Docker/ManyfoldDC/README.md) for first-run setup.

Every password and key in these files is a `CHANGE_ME` placeholder. Set real values — ideally in a `.env` file beside the compose file, referenced as `${VAR}` — before deploying.

## K8s

**`awx-operator-kustomize.yml`** — Kustomization that pulls in the AWX operator (pinned to 2.19.1) alongside an AWX custom resource, installed into the `awx-poc` namespace. Deployment commands are in the comments at the bottom of the file:

```bash
kubectl create namespace awx-poc
kubectl apply -k .
kubectl get all -n awx-poc -w
```

**`awx-deployment.yml`** — A more production-shaped AWX resource: two web and two task replicas, NFS-backed storage for Postgres and project files, resource requests and limits, and an init container that fixes the `nobody:nobody` ownership problem NFS-provisioned Postgres volumes tend to hit.

## Python

**`alan_dashboard_v3.py`** — A Flask app for tracking and applying OS patches across a fleet. It pulls the host list from a GlobalNOC CDS API (filtered by node type and tag), connects over SSH with Paramiko, detects the package manager, and reports available updates. Patching runs as a background job with a pollable status endpoint. Includes session-based auth with hashed passwords, an admin interface for user management, SQLite persistence, and TTL caching on both the host list and per-host update checks.

Configuration is entirely environment-driven. See [`.env.example`](Python/.env.example) for the full list — `FLASK_SECRET_KEY`, `GN_HOST`, `GN_USER`, `GN_PW`, `NODE_TAG_TYPE`, `ADMIN_PASSWORD`, and others. Set them; the defaults are placeholders and will not work as-is.

```bash
pip install flask paramiko requests
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python alan_dashboard_v3.py
```

**`testgrnoc_api.py`** — A minimal script for testing GlobalNOC node queries over ECP/SAML authentication. Reads all settings from the environment; exits if `GN_PW` is unset.

**`robot battle/`** — A maze-solving simulation. Robots navigate toward a goal using Manhattan distance, with collision detection and per-turn scoring. `rr.py` reads a maze from `maze_data_1.csv`, which is not included here — supply your own grid of wall, goal, and bot characters.

**`Pokemon.py`** — Pokémon and Trainer classes covering attacks, health, knockouts, revival, potions, and switching the active Pokémon. Damage is modified by a three-type Fire/Water/Grass effectiveness chart. An exercise in class design and state management.

**`greetingcards.py`**, **`exception_with_cm.py`**, **`with_context_man_ex.py`** — Context manager exercises covering both the `@contextmanager` decorator and the `__enter__`/`__exit__` protocol, including suppressing exceptions from `__exit__`.

## Terraform

Each directory holds its own `main.tf`, `variables.tf`, and `terraform.tfvars.example`.

**`k8s Provisioning/`** — Stands up a Kubernetes cluster on vSphere: configurable counts of control plane and worker VMs cloned from a template, with Linux guest customization. Node addressing is driven by `node_network_prefix`, `node_gateway`, `control_plane_ip_start`, and `worker_ip_start`, so each node gets a predictable static IP without hardcoding your subnet.

```bash
cd "Terraform/k8s Provisioning"
terraform init
terraform plan
terraform apply
```

**`pan-os Provisioning/`** — Deploys a PAN-OS firewall VM on vSphere and configures it through the `panos` provider in the same run, including an internal-traffic security rule scoped to `trust_cidr`.

Both directories ship a `terraform.tfvars.example`. Copy it to `terraform.tfvars` and fill it in — the real file is gitignored.

---

## License

[GPL-3.0](LICENSE)
