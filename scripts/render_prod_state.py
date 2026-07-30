#!/usr/bin/env python3
"""Render the independently approved and applied production component state."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

MANIFEST_PATH = Path(".github/prod-components.json")

DASHBOARD_JAVASCRIPT = r"""
(() => {
  "use strict";

  const config = window.PROD_STATE_CONFIG;
  const apiRoot = `https://api.github.com/repos/${config.repository}`;
  const refreshButton = document.getElementById("refresh-live-state");
  const sourceStatus = document.getElementById("source-status");
  const chart = document.getElementById("chart");
  const tableBody = document.getElementById("state-table-body");
  const mainLink = document.getElementById("main-link");
  const lastRefresh = document.getElementById("last-refresh");

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function api(path) {
    const response = await fetch(`${apiRoot}${path}`, {
      cache: "no-store",
      headers: {
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    });
    if (!response.ok) {
      throw new Error(`GitHub API returned ${response.status}`);
    }
    return response.json();
  }

  function currentRefs(refs) {
    const versions = new Map();
    for (const ref of refs) {
      const match = ref.ref.match(
        /^refs\/tags\/prod-current\/([^/]+)\/(approved|applied)$/
      );
      if (!match || ref.object.type !== "commit") {
        continue;
      }
      const [, component, kind] = match;
      const state = versions.get(component) || {};
      state[kind] = {sha: ref.object.sha, source: "live"};
      versions.set(component, state);
    }
    return versions;
  }

  function componentStates(refs, commits) {
    const live = currentRefs(refs);
    let usedFallback = false;
    const states = config.components.map((component) => {
      const current = live.get(component.id) || {};
      const snapshot = config.snapshot[component.id] || {};
      const approved = current.approved || snapshot.approved;
      const applied = current.applied || snapshot.applied;
      if ((!current.approved && snapshot.approved) ||
          (!current.applied && snapshot.applied)) {
        usedFallback = true;
      }
      const approvedDistance = approved
        ? commits.findIndex((commit) => commit.sha === approved.sha)
        : null;
      const appliedDistance = applied
        ? commits.findIndex((commit) => commit.sha === applied.sha)
        : null;
      let status = "Not approved or applied";
      let statusKind = "unknown";
      if (!approved && !applied) {
        status = "Not approved or applied";
      } else if (approved && !applied) {
        status = "Approved; awaiting first successful apply";
        statusKind = "pending";
      } else if (!approved && applied) {
        status = "Applied marker exists without a current approval";
        statusKind = "warning";
      } else if (approved.sha !== applied.sha) {
        status = "Newer approval is awaiting a successful apply";
        statusKind = "pending";
      } else if (approvedDistance === 0) {
        status = "Production matches its latest approval and main";
        statusKind = "current";
      } else if (approvedDistance > 0) {
        const suffix = approvedDistance === 1 ? "" : "s";
        status = `Production matches its approval; main is ` +
          `${approvedDistance} commit${suffix} ahead`;
        statusKind = "current";
      } else {
        status = "Applied commit is outside the displayed main history";
        statusKind = "warning";
      }
      return {
        ...component,
        approved,
        applied,
        approvedDistance,
        appliedDistance,
        status,
        statusKind,
      };
    });
    return {states, usedFallback};
  }

  function xPosition(distance, maxDistance) {
    const left = 225;
    const right = 1050;
    if (distance === null || distance < 0) {
      return left;
    }
    return right -
      ((Math.min(distance, maxDistance) / maxDistance) * (right - left));
  }

  function renderChart(states, commits) {
    const distances = states.flatMap((state) =>
      [state.approvedDistance, state.appliedDistance]
        .filter((distance) => distance !== null && distance >= 0)
    );
    const maxDistance = Math.max(6, ...distances);
    const rowHeight = 92;
    const height = 76 + (states.length * rowHeight);
    const lines = [
      `<svg viewBox="0 0 1100 ${height}" role="img" ` +
        `aria-label="Live production component versions across main history">`,
      '<text x="225" y="24" class="axis-label">older</text>',
      `<text x="1050" y="24" text-anchor="end" class="axis-label">` +
        `main · ${escapeHtml(commits[0].sha.slice(0, 7))}</text>`,
    ];
    const tickStep = Math.max(1, Math.ceil(maxDistance / 10));
    for (let distance = 0; distance <= maxDistance; distance += tickStep) {
      const x = xPosition(distance, maxDistance);
      const commit = commits[distance];
      lines.push(
        `<line x1="${x}" y1="36" x2="${x}" y2="${height - 18}" ` +
          'class="grid-line"/>'
      );
      if (commit) {
        lines.push(
          `<text x="${x}" y="49" text-anchor="middle" ` +
            `class="commit-label">${escapeHtml(commit.sha.slice(0, 7))}</text>`
        );
      }
    }
    states.forEach((state, index) => {
      const y = 84 + (index * rowHeight);
      const approvedX = xPosition(state.approvedDistance, maxDistance);
      const appliedX = xPosition(state.appliedDistance, maxDistance);
      lines.push(
        `<text x="12" y="${y + 5}" class="component-name">` +
          `${escapeHtml(state.name)}</text>`,
        `<line x1="225" y1="${y}" x2="1050" y2="${y}" class="rail"/>`
      );
      if (state.applied) {
        lines.push(
          `<line x1="225" y1="${y}" x2="${appliedX}" y2="${y}" ` +
            'class="applied-line"/>',
          `<circle cx="${appliedX}" cy="${y}" r="8" ` +
            'class="applied-marker"/>',
          `<text x="${appliedX}" y="${y - 15}" text-anchor="middle" ` +
            `class="marker-label">applied ` +
            `${escapeHtml(state.applied.sha.slice(0, 7))}</text>`
        );
      }
      if (state.approved) {
        if (state.applied && state.applied.sha !== state.approved.sha) {
          lines.push(
            `<line x1="${appliedX}" y1="${y}" x2="${approvedX}" y2="${y}" ` +
              'class="pending-line"/>'
          );
        }
        const markerClass = state.applied &&
          state.applied.sha === state.approved.sha
          ? "approved-current"
          : "approved-pending";
        const labelY = state.applied ? y + 27 : y - 15;
        lines.push(
          `<path d="M ${approvedX} ${y - 10} l 10 10 l -10 10 ` +
            `l -10 -10 z" class="${markerClass}"/>`,
          `<text x="${approvedX}" y="${labelY}" text-anchor="middle" ` +
            `class="marker-label">approved ` +
            `${escapeHtml(state.approved.sha.slice(0, 7))}</text>`
        );
      }
    });
    lines.push("</svg>");
    chart.innerHTML = lines.join("");
  }

  function versionLink(marker) {
    if (!marker) {
      return '<span class="muted">none</span>';
    }
    const sha = escapeHtml(marker.sha);
    return `<a href="https://github.com/${escapeHtml(config.repository)}` +
      `/commit/${sha}">${sha.slice(0, 7)}</a>`;
  }

  function renderTable(states) {
    tableBody.innerHTML = states.map((state) =>
      `<tr><th>${escapeHtml(state.name)}` +
      `<small>${escapeHtml(state.id)}</small></th>` +
      `<td>${versionLink(state.applied)}</td>` +
      `<td>${versionLink(state.approved)}</td>` +
      `<td><span class="status ${escapeHtml(state.statusKind)}">` +
      `${escapeHtml(state.status)}</span></td></tr>`
    ).join("");
  }

  async function refresh() {
    refreshButton.disabled = true;
    sourceStatus.textContent = "Loading live GitHub refs…";
    try {
      const [refs, commits] = await Promise.all([
        api("/git/matching-refs/tags/prod-current/"),
        api("/commits?sha=main&per_page=30"),
      ]);
      const {states, usedFallback} = componentStates(refs, commits);
      renderChart(states, commits);
      renderTable(states);
      const now = new Date();
      sourceStatus.textContent = usedFallback
        ? "Live refs with build-snapshot fallback"
        : "Live from GitHub refs";
      lastRefresh.textContent = `Live data refreshed ${now.toLocaleString()}`;
      mainLink.href =
        `https://github.com/${config.repository}/commit/${commits[0].sha}`;
      mainLink.textContent = commits[0].sha.slice(0, 7);
    } catch (error) {
      sourceStatus.textContent =
        `Live refresh failed; showing build snapshot (${error.message})`;
    } finally {
      refreshButton.disabled = false;
    }
  }

  refreshButton.addEventListener("click", refresh);
  refresh();
  window.setInterval(refresh, 300000);
})();
"""


class RenderError(RuntimeError):
    """Raised when production state cannot be rendered."""


@dataclass(frozen=True)
class Marker:
    tag: str
    sha: str
    tagged_at: str


@dataclass(frozen=True)
class ComponentState:
    component_id: str
    name: str
    approved: Marker | None
    applied: Marker | None
    approved_distance: int | None
    applied_distance: int | None
    status: str
    status_kind: str


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RenderError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def resolve_main() -> str:
    for candidate in ("refs/remotes/origin/main", "refs/heads/main", "HEAD"):
        resolved = git("rev-parse", "--verify", f"{candidate}^{{commit}}", check=False)
        if resolved:
            return resolved
    raise RenderError("could not resolve the main branch")


def latest_marker(prefix: str, component_id: str) -> Marker | None:
    output = git(
        "for-each-ref",
        "--sort=-taggerdate",
        "--format=%(refname:short)\t%(taggerdate:iso-strict)",
        f"refs/tags/{prefix}/{component_id}/*",
    )
    for line in output.splitlines():
        tag, separator, tagged_at = line.partition("\t")
        if not separator or not tagged_at:
            continue
        sha = git("rev-parse", f"refs/tags/{tag}^{{commit}}")
        return Marker(tag=tag, sha=sha, tagged_at=tagged_at)
    return None


def commit_distance(sha: str | None, main_sha: str) -> int | None:
    if sha is None:
        return None
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, main_sha],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if ancestor.returncode != 0:
        return None
    return int(git("rev-list", "--first-parent", "--count", f"{sha}..{main_sha}"))


def describe_status(
    approved: Marker | None,
    applied: Marker | None,
    approved_distance: int | None,
) -> tuple[str, str]:
    if approved is None and applied is None:
        return "Not approved or applied", "unknown"
    if applied is None:
        return "Approved; awaiting first successful apply", "pending"
    if approved is None:
        return "Applied marker exists without a current approval", "warning"
    if approved.sha != applied.sha:
        return "Newer approval is awaiting a successful apply", "pending"
    if approved_distance is None:
        return "Applied commit is not on main", "warning"
    if approved_distance == 0:
        return "Production matches its latest approval and main", "current"
    suffix = "" if approved_distance == 1 else "s"
    return (
        f"Production matches its approval; main is {approved_distance} commit{suffix} ahead",
        "current",
    )


def load_states(main_sha: str) -> list[ComponentState]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    states = []
    for component in manifest["components"]:
        component_id = component["id"]
        approved = latest_marker("prod-approved", component_id)
        applied = latest_marker("prod-applied", component_id)
        approved_distance = commit_distance(
            approved.sha if approved else None,
            main_sha,
        )
        applied_distance = commit_distance(
            applied.sha if applied else None,
            main_sha,
        )
        status, status_kind = describe_status(
            approved,
            applied,
            approved_distance,
        )
        states.append(
            ComponentState(
                component_id=component_id,
                name=component["name"],
                approved=approved,
                applied=applied,
                approved_distance=approved_distance,
                applied_distance=applied_distance,
                status=status,
                status_kind=status_kind,
            )
        )
    return states


def marker_link(marker: Marker | None, repository: str) -> str:
    if marker is None:
        return '<span class="muted">none</span>'
    short_sha = html.escape(marker.sha[:7])
    url = f"https://github.com/{html.escape(repository)}/commit/{marker.sha}"
    tag = html.escape(marker.tag)
    tagged_at = html.escape(marker.tagged_at)
    return f'<a href="{url}" title="{tag} · {tagged_at}">{short_sha}</a>'


def svg_position(distance: int | None, max_distance: int) -> float:
    left = 225.0
    right = 1050.0
    if distance is None:
        return left
    return right - ((min(distance, max_distance) / max_distance) * (right - left))


def render_chart(
    states: list[ComponentState],
    main_sha: str,
    history: dict[int, str],
    max_distance: int,
) -> str:
    row_height = 92
    height = 76 + (len(states) * row_height)
    lines = [
        (
            f'<svg viewBox="0 0 1100 {height}" role="img" '
            'aria-label="Production component versions across main history">'
        ),
        '<text x="225" y="24" class="axis-label">older</text>',
        (
            f'<text x="1050" y="24" text-anchor="end" class="axis-label">'
            f'main · {html.escape(main_sha[:7])}</text>'
        ),
    ]
    tick_step = max(1, math.ceil(max_distance / 10))
    for distance in range(0, max_distance + 1, tick_step):
        x = svg_position(distance, max_distance)
        short_sha = history.get(distance)
        lines.append(
            f'<line x1="{x:.1f}" y1="36" x2="{x:.1f}" y2="{height - 18}" '
            'class="grid-line"/>'
        )
        if short_sha:
            lines.append(
                f'<text x="{x:.1f}" y="49" text-anchor="middle" '
                f'class="commit-label">{html.escape(short_sha)}</text>'
            )

    for index, state in enumerate(states):
        y = 84 + (index * row_height)
        approved_x = svg_position(state.approved_distance, max_distance)
        applied_x = svg_position(state.applied_distance, max_distance)
        lines.extend(
            [
                (
                    f'<text x="12" y="{y + 5}" class="component-name">'
                    f"{html.escape(state.name)}</text>"
                ),
                f'<line x1="225" y1="{y}" x2="1050" y2="{y}" class="rail"/>',
            ]
        )
        if state.applied:
            lines.append(
                f'<line x1="225" y1="{y}" x2="{applied_x:.1f}" y2="{y}" '
                'class="applied-line"/>'
            )
            lines.append(
                f'<circle cx="{applied_x:.1f}" cy="{y}" r="8" '
                'class="applied-marker"/>'
            )
            lines.append(
                f'<text x="{applied_x:.1f}" y="{y - 15}" text-anchor="middle" '
                f'class="marker-label">applied {html.escape(state.applied.sha[:7])}'
                "</text>"
            )
        if state.approved:
            if state.applied and state.applied.sha != state.approved.sha:
                lines.append(
                    f'<line x1="{applied_x:.1f}" y1="{y}" '
                    f'x2="{approved_x:.1f}" y2="{y}" class="pending-line"/>'
                )
            marker_class = (
                "approved-current"
                if state.applied and state.applied.sha == state.approved.sha
                else "approved-pending"
            )
            lines.append(
                f'<path d="M {approved_x:.1f} {y - 10} l 10 10 l -10 10 '
                f'l -10 -10 z" class="{marker_class}"/>'
            )
            label_y = y + 27 if state.applied else y - 15
            lines.append(
                f'<text x="{approved_x:.1f}" y="{label_y}" text-anchor="middle" '
                f'class="marker-label">approved '
                f"{html.escape(state.approved.sha[:7])}</text>"
            )
    lines.append("</svg>")
    return "\n".join(lines)


def render_html(
    states: list[ComponentState],
    main_sha: str,
    repository: str,
    generated_at: str,
) -> str:
    live_config = {
        "repository": repository,
        "components": [
            {"id": state.component_id, "name": state.name} for state in states
        ],
        "snapshot": {
            state.component_id: {
                "approved": {"sha": state.approved.sha} if state.approved else None,
                "applied": {"sha": state.applied.sha} if state.applied else None,
            }
            for state in states
        },
    }
    live_config_json = json.dumps(live_config, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    distances = [
        distance
        for state in states
        for distance in (state.approved_distance, state.applied_distance)
        if distance is not None
    ]
    max_distance = max([6, *distances])
    history_output = git(
        "log",
        "--first-parent",
        f"--max-count={max_distance + 1}",
        "--format=%h",
        main_sha,
    )
    history = {
        distance: short_sha
        for distance, short_sha in enumerate(history_output.splitlines())
    }
    chart = render_chart(states, main_sha, history, max_distance)
    rows = []
    for state in states:
        rows.append(
            "<tr>"
            f"<th>{html.escape(state.name)}"
            f'<small>{html.escape(state.component_id)}</small></th>'
            f"<td>{marker_link(state.applied, repository)}</td>"
            f"<td>{marker_link(state.approved, repository)}</td>"
            f'<td><span class="status {state.status_kind}">'
            f"{html.escape(state.status)}</span></td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Production component state</title>
  <style>
    :root {{
      color-scheme: dark;
      --background: #0d1117;
      --panel: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --green: #3fb950;
      --amber: #d29922;
      --red: #f85149;
      --blue: #58a6ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--background);
      color: var(--text);
      font: 15px/1.5 system-ui, sans-serif;
    }}
    main {{ width: min(1240px, calc(100% - 32px)); margin: 48px auto; }}
    h1 {{ margin-bottom: 4px; font-size: clamp(26px, 4vw, 42px); }}
    .subtitle, .muted, small {{ color: var(--muted); }}
    .panel {{
      margin-top: 24px;
      padding: 20px;
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: var(--panel);
    }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 18px; margin-bottom: 8px; }}
    .legend span::before {{
      display: inline-block;
      width: 11px;
      height: 11px;
      margin-right: 7px;
      border-radius: 50%;
      content: "";
      background: var(--muted);
    }}
    .legend .applied::before {{ background: var(--green); }}
    .legend .approved::before {{ background: var(--amber); }}
    .live-controls {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}
    button {{
      padding: 7px 12px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: #21262d;
      color: var(--text);
      cursor: pointer;
    }}
    button:disabled {{ cursor: wait; opacity: .6; }}
    svg {{ min-width: 820px; width: 100%; height: auto; }}
    svg text {{ fill: var(--text); font-family: system-ui, sans-serif; }}
    .axis-label, .commit-label, .marker-label {{ fill: var(--muted); }}
    .commit-label, .marker-label {{ font-size: 11px; }}
    .component-name {{ font-size: 15px; font-weight: 650; }}
    .grid-line {{ stroke: var(--border); stroke-dasharray: 3 5; }}
    .rail {{ stroke: #484f58; stroke-width: 6; stroke-linecap: round; }}
    .applied-line {{ stroke: var(--green); stroke-width: 7; }}
    .pending-line {{ stroke: var(--amber); stroke-width: 7; }}
    .applied-marker, .approved-current {{ fill: var(--green); }}
    .approved-pending {{ fill: var(--amber); }}
    table {{ width: 100%; min-width: 760px; border-collapse: collapse; }}
    th, td {{ padding: 14px; border-bottom: 1px solid var(--border); text-align: left; }}
    th {{ width: 25%; }}
    th small {{ display: block; font-weight: 400; }}
    a {{ color: var(--blue); font-family: ui-monospace, monospace; }}
    .status {{ display: inline-flex; align-items: center; gap: 7px; }}
    .status::before {{
      width: 9px; height: 9px; border-radius: 50%; content: "";
      background: var(--muted);
    }}
    .status.current::before {{ background: var(--green); }}
    .status.pending::before {{ background: var(--amber); }}
    .status.warning::before {{ background: var(--red); }}
    footer {{ margin: 20px 2px; color: var(--muted); }}
  </style>
</head>
<body>
  <main>
    <h1>Production component state</h1>
    <p class="subtitle">
      What each independently promoted playbook is approved to run, and what
      most recently completed successfully.
    </p>
    <section class="panel">
      <div class="live-controls">
        <strong id="source-status">Build snapshot; loading live refs…</strong>
        <button id="refresh-live-state" type="button">Refresh live state</button>
      </div>
      <div class="legend">
        <span class="applied">Successfully applied</span>
        <span class="approved">Approved, not yet applied</span>
        <span>Main history</span>
      </div>
      <div id="chart">{chart}</div>
    </section>
    <section class="panel">
      <table>
        <thead>
          <tr>
            <th>Playbook</th>
            <th>Last applied</th>
            <th>Latest approved</th>
            <th>State</th>
          </tr>
        </thead>
        <tbody id="state-table-body">
          {''.join(rows)}
        </tbody>
      </table>
    </section>
    <footer>
      Main <a id="main-link"
      href="https://github.com/{html.escape(repository)}/commit/{main_sha}">
      {html.escape(main_sha[:7])}</a> ·
      <span id="last-refresh">Build snapshot generated
      {html.escape(generated_at)}</span> ·
      <a href="state.json">build snapshot JSON</a>
    </footer>
  </main>
  <script>window.PROD_STATE_CONFIG = {live_config_json};</script>
  <script src="dashboard.js"></script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("_site"))
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", "sevensteves-com/cicd-ansible-promotion-lab"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    main_sha = resolve_main()
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    states = load_states(main_sha)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "index.html").write_text(
        render_html(states, main_sha, args.repository, generated_at),
        encoding="utf-8",
    )
    (args.output_dir / "dashboard.js").write_text(
        DASHBOARD_JAVASCRIPT.strip() + "\n",
        encoding="utf-8",
    )
    raw_state = {
        "generated_at": generated_at,
        "repository": args.repository,
        "main_sha": main_sha,
        "components": [asdict(state) for state in states],
    }
    (args.output_dir / "state.json").write_text(
        json.dumps(raw_state, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Rendered {len(states)} production components to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
