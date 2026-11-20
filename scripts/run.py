# Copyright 2024-2026 Qubernetes
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Entry point for the composite action; all inputs arrive through environment variables."""

import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError

from sdlc_config import Config

OUTPUTS = {
    "branch": "",
    "pull-request-number": "",
    "pull-request-url": "",
    "spec-path": "",
    "adr-path": "",
    "updated-issues": 0,
}


def load_operation(operation):
    name = (
        "scaffold-issue-docs.py"
        if operation == "scaffold"
        else "update-merged-doc-links.py"
    )
    spec = importlib.util.spec_from_file_location(
        "sdlc_operation", Path(__file__).with_name(name)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def boolean_input(name):
    value = os.environ.get(name, "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def configuration(event, module):
    branch = os.environ.get("SDLC_BASE_BRANCH", "").strip()
    if not branch:
        branch = event.get("repository", {}).get("default_branch")
    if not branch:
        repository = os.environ["GITHUB_REPOSITORY"]
        branch = module.api_request("GET", f"/repos/{repository}")["default_branch"]
    templates = os.environ.get("SDLC_TEMPLATES_DIRECTORY", "").strip()
    template_path = None
    if templates:
        workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
        template_path = (workspace / templates).resolve()
        if not template_path.is_relative_to(workspace) or not template_path.is_dir():
            raise ValueError(
                "templates-directory must be an existing directory inside GITHUB_WORKSPACE"
            )
    return Config(
        base_branch=branch,
        specs_directory=os.environ.get("SDLC_SPECS_DIRECTORY", "doc/specs"),
        adrs_directory=os.environ.get("SDLC_ADRS_DIRECTORY", "doc/adrs"),
        spec_label=os.environ.get("SDLC_SPEC_LABEL", "needs-spec"),
        adr_label=os.environ.get("SDLC_ADR_LABEL", "needs-adr"),
        templates_directory=template_path,
    )


def execute(event):
    operation = os.environ.get("SDLC_OPERATION", "").strip()
    if operation not in {"scaffold", "sync-links"}:
        raise ValueError("operation must be scaffold or sync-links")
    dry_run = boolean_input("SDLC_DRY_RUN")
    if dry_run and operation != "sync-links":
        raise ValueError("dry-run is supported only for sync-links")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    issue_number = os.environ.get("SDLC_ISSUE_NUMBER", "").strip() or None
    if operation == "scaffold":
        if event_name not in {"issues", "workflow_dispatch"}:
            print(
                "Skipping: scaffolding requires an issues or workflow_dispatch event."
            )
            return dict(OUTPUTS)
        if event_name == "workflow_dispatch" and issue_number is None:
            raise ValueError("issue-number is required for a manual scaffolding run")
        if event_name == "issues":
            if event.get("action") not in {"opened", "reopened", "labeled"}:
                return dict(OUTPUTS)
            issue = event.get("issue", {})
            if issue.get("state") == "closed" or "pull_request" in issue:
                return dict(OUTPUTS)
            if event.get("action") == "labeled" and event.get("label", {}).get(
                "name"
            ) not in {
                os.environ.get("SDLC_SPEC_LABEL", "needs-spec"),
                os.environ.get("SDLC_ADR_LABEL", "needs-adr"),
            }:
                return dict(OUTPUTS)
            # An explicit issue-number must not redirect an issue event to another issue.
            issue_number = None
    else:
        pull = event.get("pull_request", {})
        if event_name not in {"pull_request", "pull_request_target"} or (
            event.get("action") != "closed" or not pull.get("merged")
        ):
            print(
                "Skipping: link synchronization requires a merged pull request event."
            )
            return dict(OUTPUTS)
    if not os.environ.get("GITHUB_TOKEN", "").strip():
        raise ValueError("token is required")
    if not os.environ.get("GITHUB_REPOSITORY", "").strip():
        raise ValueError("GITHUB_REPOSITORY is required")
    module = load_operation(operation)
    module.CONFIG = configuration(event, module)
    outputs = dict(OUTPUTS)
    if operation == "scaffold":
        outputs.update(module.scaffold(event, issue_number) or {})
    else:
        outputs["updated-issues"] = module.update_issues(event, dry_run=dry_run) or 0
    return outputs


def write_outputs(outputs):
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    lines = []
    for key in OUTPUTS:
        value = str(outputs.get(key, ""))
        if "\n" in value or "\r" in value:
            raise ValueError(f"Unexpected multiline action output: {key}")
        lines.append(f"{key}={value}\n")
    with Path(destination).open("a", encoding="utf-8") as output:
        output.write("".join(lines))


def main():
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        write_outputs(execute(event))
    except Exception as error:
        if isinstance(error, HTTPError):
            detail = error.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("message", error.reason)
            except (ValueError, AttributeError):
                detail = error.reason
            message = f"GitHub API returned {error.code}: {detail}"
            if error.code == 403 and "/pulls" in error.url:
                message += (
                    ". Check pull-requests: write and Settings > Actions > General > "
                    "Allow GitHub Actions to create and approve pull requests."
                )
        else:
            message = str(error)
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
