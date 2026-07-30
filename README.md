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
candidate SHA. After they pass, the workflow creates an approval run only for
each component whose production inputs differ from its last successful apply:

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

Production approval runs are created only when a component differs from its
latest successfully applied SHA. The comparison always includes its playbook,
production inventory, and manifest deployment settings. Declare additional
role, template, or shared-code paths relative to `ansible/` when needed:

```json
"dependency_paths": [
  "roles/nginx_proxy",
  "templates/shared"
]
```

Nonproduction still reconciles every declared component at the whole candidate
SHA. Change detection affects only which production approval runs are offered.
Components without an applied tag are always offered for their first deploy.

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
5. Confirm only
   `prod-approved/nginx-proxy/<run-id>-<attempt>-<sha>` is created and only
   the nginx playbook reconciles.
6. Approve `idmz-base` later and confirm it reconciles independently.

## Scheduled Reconciliation

The daily 03:00 UTC workflow trigger is disabled by the manifest:

```json
"scheduled_reconcile_enabled": false
```

Setting it to `true` reconciles every component at its independently latest
annotated approval tag. It never promotes the current `main` SHA implicitly.

## Production State Page

The production workflow records two different immutable facts:

- `prod-approved/<component>/<run-id>-<attempt>-<short-sha>` means that exact
  component version passed the production approval gate.
- `prod-applied/<component>/<run-id>-<attempt>-<short-sha>` is created only
  after that component's Ansible run succeeds.

The run ID makes every approval an ordered event, including re-approval of an
older SHA for rollback. Existing short-SHA-only approval tags remain valid.

The GitHub Pages dashboard queries the immutable tags, waiting production
approval workflow runs, and recent `main` commits directly from GitHub's
public API when it loads, every five minutes, and when **Refresh live state**
is selected. It displays awaiting approval, approved, and successfully
applied as separate states. Pages deployment is therefore only for dashboard
code changes, not production data changes. The build-time `state.json` is a
fallback snapshot for legacy tags, not the live data source.

After merging this feature, select **GitHub Actions** under
**Settings → Pages → Build and deployment → Source**, then manually run
**Publish Prod State** once. The page will be available at:

<https://sevensteves-com.github.io/cicd-ansible-promotion-lab/>

The workflow uses a `prod-state-dashboard` deployment Environment. It is
unrelated to the shared `prod` approval Environment and is not an Environment
per playbook.

## Local Validation

```sh
python scripts/prod_components.py validate
python scripts/render_prod_state.py --output-dir /tmp/prod-state-site
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
