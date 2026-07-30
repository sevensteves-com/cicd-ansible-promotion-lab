# CI/CD Ansible Promotion Lab

This public lab mirrors the promotion environments and component names used by
`or-platform-idmz-infra`, while every playbook safely targets localhost on a
GitHub-hosted runner.

## Environment Flow

```text
nonprod-base → dev → test → component approval → prod
```

- `nonprod-base`, `dev`, `test`, and `prod` are Ansible inventory directories.
- `prod` is also the one protected GitHub Environment used as the production
  approval gate.
- There is not one GitHub Environment per playbook.

Both lab components run through all three nonproduction inventories at the
candidate SHA. After they pass, the workflow automatically creates a separate
approval workflow run for each component:

```text
Approve idmz-base at <sha>     → shared GitHub Environment: prod
Approve nginx-proxy at <sha>   → shared GitHub Environment: prod
```

Approving one run creates its component tag and reconciles only that playbook.

## Component Declaration

`.github/prod-components.json` is the production declaration. It currently
maps:

| Component | Playbook | Production inventory |
| --- | --- | --- |
| `idmz-base` | `playbooks/idmz_base.yml` | `inventory/prod/hosts.yml` |
| `nginx-proxy` | `playbooks/nginx-proxy.yml` | `inventory/prod/hosts.yml` |

Add or edit components in the manifest instead of copying workflow steps.

## GitHub Setup

1. Make the repository public.
2. Create one repository Environment named `prod`.
3. Add yourself as a required reviewer and leave self-review enabled for this
   solo lab.

Every workflow job uses `ubuntu-latest`. No self-hosted runner is required.

The workflows explicitly request `contents: write` when creating approval tags
and `actions: write` when dispatching another workflow. The repository-wide
default workflow permission does not need to be made permissive unless an
organization policy prevents these explicit permissions.

## Test Independent Promotion

1. Change `release_marker` in `playbooks/idmz_base.yml`, merge it, and leave
   the `idmz-base` approval run waiting.
2. Change `release_marker` in `playbooks/nginx-proxy.yml` in a later commit.
3. Merge it and wait for `nonprod-base → dev → test`.
4. Approve only the new `nginx-proxy` approval run.
5. Confirm only `prod-approved/nginx-proxy/<sha>` is created and only the
   nginx playbook reconciles.
6. Approve `idmz-base` later and confirm it reconciles independently.

## Scheduled Reconciliation

The daily 03:00 UTC workflow trigger is disabled by the manifest:

```json
"scheduled_reconcile_enabled": false
```

Setting it to `true` reconciles every component at its independently latest
annotated approval tag. It never promotes the current `main` SHA implicitly.

## Local Validation

```sh
python scripts/prod_components.py validate
ruff check scripts/
yamllint .github/workflows ansible
ansible-playbook -i ansible/inventory/nonprod-base/hosts.yml \
  ansible/playbooks/idmz_base.yml --syntax-check
ansible-playbook -i ansible/inventory/dev/hosts.yml \
  ansible/playbooks/idmz_base.yml --syntax-check
ansible-playbook -i ansible/inventory/test/hosts.yml \
  ansible/playbooks/idmz_base.yml --syntax-check
ansible-playbook -i ansible/inventory/prod/hosts.yml \
  ansible/playbooks/idmz_base.yml --syntax-check
ansible-playbook -i ansible/inventory/prod/hosts.yml \
  ansible/playbooks/nginx-proxy.yml --syntax-check
```
