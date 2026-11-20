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

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run as action  # noqa: E402 - standalone scripts live outside the tests directory
from sdlc_config import Config  # noqa: E402
from test_scaffold_issue_docs import (  # noqa: E402
    EVENT,
    FakeGitHub,
    ISSUE,
    PREFIX,
    REPOSITORY,
    scaffold,
)
from test_update_merged_doc_links import PR, sync  # noqa: E402


class TestConfiguration(unittest.TestCase):
    def test_default_branch_comes_from_event(self):
        with patch.dict(os.environ, {}, clear=True):
            config = action.configuration(
                {"repository": {"default_branch": "trunk"}}, None
            )
        self.assertEqual(config.base_branch, "trunk")

    def test_default_branch_falls_back_to_repository_api(self):
        module = SimpleNamespace(
            api_request=lambda method, path: {"default_branch": "develop"}
        )
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": REPOSITORY}, clear=True):
            self.assertEqual(action.configuration({}, module).base_branch, "develop")

    def test_explicit_base_branch_overrides_repository_default(self):
        with patch.dict(
            os.environ, {"SDLC_BASE_BRANCH": "release/current"}, clear=True
        ):
            config = action.configuration(
                {"repository": {"default_branch": "trunk"}}, None
            )
        self.assertEqual(config.base_branch, "release/current")

    def test_invalid_or_overlapping_document_directories_fail(self):
        for directory in (
            "",
            ".",
            "/docs",
            "../docs",
            "docs/../specs",
            "docs//specs",
            "docs\\specs",
        ):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                Config(specs_directory=directory)
        with self.assertRaises(ValueError):
            Config(specs_directory="docs", adrs_directory="docs/adrs")

    def test_labels_must_be_distinct_and_nonempty(self):
        for values in (
            {"spec_label": " "},
            {"spec_label": "same", "adr_label": "same"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values)

    def test_template_overrides_are_per_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            overrides = workspace / "templates"
            overrides.mkdir()
            (overrides / "spec.md").write_text(
                "---\nissue: $number\n---\nCustom $title\n"
            )
            with patch.dict(
                os.environ,
                {
                    "GITHUB_WORKSPACE": directory,
                    "SDLC_TEMPLATES_DIRECTORY": "templates",
                },
                clear=True,
            ):
                config = action.configuration(
                    {"repository": {"default_branch": "trunk"}}, None
                )
            with patch.object(scaffold, "CONFIG", config):
                docs, additions = scaffold.plan_documents(
                    ISSUE, ["spec", "adr"], set(), set(), "today"
                )
            self.assertIn("Custom Validate targets", additions[docs["spec"]])
            self.assertIn("## Decision", additions[docs["adr"]])

    def test_template_without_issue_metadata_fails_before_document_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            templates = Path(directory)
            (templates / "spec.md").write_text("# $title\n")
            with patch.object(
                scaffold, "CONFIG", Config(templates_directory=templates)
            ):
                with self.assertRaisesRegex(ValueError, "template must include issue"):
                    scaffold.plan_documents(ISSUE, ["spec"], set(), set(), "today")

    def test_template_directory_must_stay_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            with patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": str(workspace), "SDLC_TEMPLATES_DIRECTORY": ".."},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "inside GITHUB_WORKSPACE"):
                    action.configuration(
                        {"repository": {"default_branch": "trunk"}}, None
                    )


class TestActionExecution(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "SDLC_OPERATION": "scaffold",
                "GITHUB_TOKEN": "fake-token",
                "GITHUB_EVENT_NAME": "issues",
                "GITHUB_REPOSITORY": REPOSITORY,
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.original_config = scaffold.CONFIG
        self.original_sync_config = sync.CONFIG
        self.addCleanup(setattr, scaffold, "CONFIG", self.original_config)
        self.addCleanup(setattr, sync, "CONFIG", self.original_sync_config)

    def test_scaffolds_with_custom_paths_labels_branch_and_outputs(self):
        github = FakeGitHub()
        github.refs["release/trunk"] = github.refs.pop("main")
        github.trees["base-tree"] = {
            path.replace("doc/adrs/", "architecture/decisions/"): content
            for path, content in github.trees["base-tree"].items()
        }
        github.issue["labels"] = [{"name": "specification"}, {"name": "architecture"}]
        event = dict(
            EVENT,
            label={"name": "specification"},
            repository={"default_branch": "release/trunk"},
        )
        os.environ.update(
            {
                "SDLC_SPEC_LABEL": "specification",
                "SDLC_ADR_LABEL": "architecture",
                "SDLC_SPECS_DIRECTORY": "design/specifications",
                "SDLC_ADRS_DIRECTORY": "architecture/decisions",
            }
        )
        with patch.object(
            action, "load_operation", return_value=scaffold
        ), patch.object(scaffold, "api_request", side_effect=github.request):
            outputs = action.execute(event)
            self.assertEqual(action.execute(event)["pull-request-number"], 1)
        self.assertEqual(outputs["branch"], "123-validate-targets")
        self.assertEqual(
            outputs["spec-path"], "design/specifications/validate-targets.md"
        )
        self.assertEqual(
            outputs["adr-path"], "architecture/decisions/0006-validate-targets.md"
        )
        self.assertEqual(outputs["pull-request-number"], 1)
        self.assertTrue(outputs["pull-request-url"].endswith("/pull/1"))
        files = github.files(outputs["branch"])
        self.assertIn(
            "../../architecture/decisions/0006-validate-targets.md",
            files[outputs["spec-path"]],
        )
        self.assertEqual(github.pulls[0]["base"], "release/trunk")
        self.assertEqual(len(github.pulls), 1)

    def test_manual_run_uses_explicit_issue_number(self):
        github = FakeGitHub()
        os.environ.update(
            {"GITHUB_EVENT_NAME": "workflow_dispatch", "SDLC_ISSUE_NUMBER": "123"}
        )
        with patch.object(
            action, "load_operation", return_value=scaffold
        ), patch.object(scaffold, "api_request", side_effect=github.request):
            outputs = action.execute({"repository": {"default_branch": "main"}})
        self.assertEqual(outputs["branch"], "123-validate-targets")

    def test_manual_run_requires_issue_number(self):
        os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        with self.assertRaisesRegex(ValueError, "issue-number is required"):
            action.execute({})

    def test_issue_number_input_cannot_redirect_an_issue_event(self):
        github = FakeGitHub()
        os.environ["SDLC_ISSUE_NUMBER"] = "456"
        with patch.object(
            action, "load_operation", return_value=scaffold
        ), patch.object(scaffold, "api_request", side_effect=github.request):
            outputs = action.execute(dict(EVENT, repository={"default_branch": "main"}))
        self.assertEqual(outputs["branch"], "123-validate-targets")

    def test_unrelated_issue_labels_skip_without_api_access(self):
        with patch.object(action, "load_operation") as load:
            outputs = action.execute(dict(EVENT, label={"name": "unrelated"}))
        self.assertEqual(outputs, action.OUTPUTS)
        load.assert_not_called()

    def test_unsupported_events_skip_without_api_access(self):
        for operation, event_name, event in (
            ("scaffold", "push", {}),
            ("sync-links", "push", {}),
            (
                "sync-links",
                "pull_request_target",
                {"action": "closed", "pull_request": {"merged": False}},
            ),
        ):
            with self.subTest(operation=operation, event_name=event_name):
                os.environ.update(
                    {"SDLC_OPERATION": operation, "GITHUB_EVENT_NAME": event_name}
                )
                with patch.object(action, "load_operation") as load:
                    self.assertEqual(action.execute(event), action.OUTPUTS)
                load.assert_not_called()

    def test_missing_token_fails_before_any_api_call(self):
        os.environ["GITHUB_TOKEN"] = ""
        with self.assertRaisesRegex(ValueError, "token is required"):
            action.execute(EVENT)

    def test_invalid_operation_and_unsafe_dry_run_fail(self):
        os.environ["SDLC_OPERATION"] = "unknown"
        with self.assertRaisesRegex(ValueError, "operation must be"):
            action.execute(EVENT)
        os.environ.update({"SDLC_OPERATION": "scaffold", "SDLC_DRY_RUN": "true"})
        with self.assertRaisesRegex(ValueError, "only for sync-links"):
            action.execute(EVENT)
        os.environ["SDLC_DRY_RUN"] = "yes"
        with self.assertRaisesRegex(ValueError, "true or false"):
            action.execute(EVENT)

    def test_sync_links_uses_configured_directories_and_base_branch(self):
        pull = copy.deepcopy(PR)
        pull["base"]["ref"] = "develop"
        event = {
            "action": "closed",
            "pull_request": pull,
            "repository": {"default_branch": "develop"},
        }
        os.environ.update(
            {
                "SDLC_OPERATION": "sync-links",
                "GITHUB_EVENT_NAME": "pull_request_target",
                "SDLC_SPECS_DIRECTORY": "design/specifications",
                "SDLC_ADRS_DIRECTORY": "architecture/decisions",
            }
        )
        body = (
            f"https://github.com/{REPOSITORY}/blob/feature/targets/"
            "design/specifications/change.md"
        )
        with patch.object(action, "load_operation", return_value=sync), patch.object(
            sync, "linked_issues", return_value=[123]
        ), patch.object(sync, "api_request", return_value={"body": body}) as api:
            outputs = action.execute(event)
        self.assertEqual(outputs["updated-issues"], 1)
        self.assertEqual(
            api.call_args_list[-1].args[2]["body"],
            body.replace("feature/targets/", "develop/"),
        )

    def test_sync_links_dry_run_reports_changes_without_writing(self):
        event = {
            "action": "closed",
            "pull_request": PR,
            "repository": {"default_branch": "main"},
        }
        body = (
            f"https://github.com/{REPOSITORY}/blob/feature/targets/doc/specs/change.md"
        )
        os.environ.update(
            {
                "SDLC_OPERATION": "sync-links",
                "GITHUB_EVENT_NAME": "pull_request_target",
                "SDLC_DRY_RUN": "true",
            }
        )
        with patch.object(action, "load_operation", return_value=sync), patch.object(
            sync, "linked_issues", return_value=[123]
        ), patch.object(sync, "api_request", return_value={"body": body}) as api:
            self.assertEqual(action.execute(event)["updated-issues"], 1)
        api.assert_called_once_with("GET", f"{PREFIX}/issues/123")

    def test_outputs_are_written_without_injecting_extra_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                action.write_outputs(dict(action.OUTPUTS, branch="work/123"))
                self.assertIn("branch=work/123\n", output.read_text())
                with self.assertRaisesRegex(ValueError, "multiline action output"):
                    action.write_outputs(
                        dict(action.OUTPUTS, branch="unsafe\ninjected=value")
                    )

    def test_entrypoint_reports_failure_as_escaped_annotation(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps(EVENT))
            error_output = io.StringIO()
            with patch.dict(
                os.environ, {"GITHUB_EVENT_PATH": str(event)}
            ), patch.object(
                action, "execute", side_effect=ValueError("bad%input\nsecond line")
            ), contextlib.redirect_stderr(
                error_output
            ):
                self.assertEqual(action.main(), 1)
            self.assertEqual(
                error_output.getvalue(), "::error::bad%25input%0Asecond line\n"
            )

    def test_rest_and_graphql_use_distinct_enterprise_endpoints(self):
        os.environ.update(
            {
                "GITHUB_API_URL": "https://git.example/api/v3",
                "GITHUB_GRAPHQL_URL": "https://git.example/api/graphql",
            }
        )
        for module in (scaffold, sync):
            for path, expected in (
                ("/graphql", "https://git.example/api/graphql"),
                ("/repos/org/repo", "https://git.example/api/v3/repos/org/repo"),
            ):
                with self.subTest(module=module.__name__, path=path):
                    with patch.object(
                        module, "urlopen", return_value=io.BytesIO(b"{}")
                    ) as open_url:
                        module.api_request("POST", path, {})
                    self.assertEqual(open_url.call_args.args[0].full_url, expected)


class TestStandalonePackage(unittest.TestCase):
    def test_entrypoint_runs_from_copied_package_without_repository_checkout(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "action"
            shutil.copytree(
                source, package, ignore=shutil.ignore_patterns("__pycache__", ".venv")
            )
            workspace = Path(directory) / "empty-workspace"
            workspace.mkdir()
            event = workspace / "event.json"
            event.write_text("{}")
            output = workspace / "output"
            environment = dict(
                os.environ,
                SDLC_OPERATION="sync-links",
                SDLC_DRY_RUN="false",
                GITHUB_EVENT_NAME="push",
                GITHUB_EVENT_PATH=str(event),
                GITHUB_OUTPUT=str(output),
                GITHUB_WORKSPACE=str(workspace),
                PYTHONDONTWRITEBYTECODE="1",
            )
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, str(package / "scripts/run.py")],
                cwd=workspace,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("updated-issues=0", output.read_text())


if __name__ == "__main__":
    unittest.main()
