# SDLC action

Create feature specifications and Architecture Decision Records from issue labels, then update their issue links after merging. This composite action bundles the Python scripts and Markdown templates in one versioned repository. It uses Python's standard library and GitHub's API; consumers do not need to install this project's Python packages or copy its scripts.

This directory is prepared for extraction into its own repository. The examples assume the future repository is `qubernetes-dev/sdlc-action`; replace that name with your chosen owner and repository, and publish a release before using the examples.

## Operations

| Operation | Trigger | Result |
| --- | --- | --- |
| `scaffold` | An issue is opened, reopened, or labeled; or a manual run with an issue number | Create missing document templates on the issue's branch, open or reuse a draft PR, and add links to the issue |
| `sync-links` | A PR is merged into the configured base branch | Update matching specification and ADR links in the bodies of issues GitHub associates with the PR for closure |

The action creates branches as `<issue-number>-<title-slug>` and reuses any existing issue-linked branch regardless of its name. It finds an existing scaffolding PR through a hidden issue marker when GitHub has replaced the original branch association with the PR association. Keep that marker when editing a PR body. Multiple candidate branches cause an error instead of an arbitrary selection.

New PRs are drafts titled `feat: <issue title>`. Their visible description is a related issue link and `TODO: Describe the changes.` Existing PR titles and descriptions are preserved. GitHub automatically associates PRs created from issue-linked branches, so merging can close the issue without a closing keyword in the description. See [GitHub's linked branch behavior](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-a-branch-for-an-issue).

Specifications use `<title-slug>.md`; ADRs use `<sequential-number>-<title-slug>.md`. Both contain an integer `issue` field in YAML front matter. Reruns use that metadata to find renamed documents and preserve existing content. Older issue-number filenames are also recognized. An unrelated specification with the same filename causes an error. ADR numbers are reserved across repository branches; the consuming workflow must serialize scaffolding runs to prevent concurrent allocation.

## Adoption

1. Publish this action repository and a release, such as `v1.0.0`. Use a release tag or pinned commit in consumers; the examples use the major release tag `v1`.
2. Copy [`examples/scaffold.yml`](examples/scaffold.yml) and [`examples/sync-links.yml`](examples/sync-links.yml) into the consuming repository's `.github/workflows/` directory and replace the action owner and repository if needed.
3. Create `needs-spec` and `needs-adr` labels, or configure your preferred labels through inputs.
4. Enable **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests**. Organization policies must allow the consuming workflow's requested permissions.
5. Merge the caller workflows into the consuming repository's default branch, then label a test issue. Issue events require the workflow on the default branch.

A scaffolding step looks like this after the action is published:

```yaml
- uses: qubernetes-dev/sdlc-action@v1
  id: sdlc
  with:
    operation: scaffold
```

The `scaffold` workflow needs `contents: write`, `issues: write`, and `pull-requests: write`. The `sync-links` workflow needs `contents: read`, `issues: write`, and `pull-requests: read`. These permissions belong in the caller workflow; an action cannot grant itself permissions.

No consuming repository checkout is required for either operation with the bundled templates. Scripts are executed from `github.action_path`, and GitHub API calls operate on `GITHUB_REPOSITORY`. The action never checks out or executes issue-branch or PR-branch code. The link workflow uses `pull_request_target` so it can update issue bodies after merging fork PRs, and executes only the selected version of the action.

`GITHUB_TOKEN` writes do not trigger branch-push workflows. GitHub requires approval for PR workflow runs created by that token. See [GitHub's token behavior](https://docs.github.com/en/actions/concepts/security/github_token).

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `operation` | Required | `scaffold` or `sync-links` |
| `token` | `${{ github.token }}` | Token scoped to the consuming repository |
| `issue-number` | Empty | Required for manual scaffolding runs; issue events always use their own issue |
| `base-branch` | Repository default branch | PR target and destination for promoted links |
| `specs-directory` | `doc/specs` | Repository-relative specification directory |
| `adrs-directory` | `doc/adrs` | Repository-relative ADR directory |
| `spec-label` | `needs-spec` | Label requesting a specification |
| `adr-label` | `needs-adr` | Label requesting an ADR |
| `templates-directory` | Empty | Checked-out directory containing optional `spec.md` and/or `adr.md` overrides |
| `dry-run` | `false` | For `sync-links` only; report changes without writing issue bodies |

Directory inputs must be normalized repository-relative paths. Specification and ADR directories must be distinct and must not contain one another. Labels must be distinct and nonempty. The same directory and base-branch inputs must be used by both caller workflows. The default branch is read from the event or, when unavailable, from repository metadata. Custom GitHub hosts use `GITHUB_API_URL`, `GITHUB_GRAPHQL_URL`, and `GITHUB_SERVER_URL`.

For example:

```yaml
with:
  operation: scaffold
  specs-directory: design/specifications
  adrs-directory: architecture/decisions
  spec-label: specification
  adr-label: architecture
```

## Templates

Bundled templates live in [`scripts/templates/`](scripts/templates/). To override a template, check out trusted files from the consuming repository's default branch before running the action, then set `templates-directory`. The directory must resolve inside `GITHUB_WORKSPACE`. Each missing override file falls back to its bundled template.

```yaml
- uses: actions/checkout@v4
  with:
    ref: ${{ github.event.repository.default_branch }}
    persist-credentials: false
- uses: qubernetes-dev/sdlc-action@v1
  with:
    operation: scaffold
    templates-directory: .github/sdlc-templates
```

Templates use Python `string.Template` placeholders: `$number`, `$title`, `$summary` (a JSON-quoted YAML string), `$issue_url`, `$date`, `$related` (a relative Markdown link or TODO), `$description` (quoted issue text), and `$adr_number` (ADR templates only). Keep `issue: $number` in YAML front matter so reruns can find the generated document. Templates missing that association fail before any branch or document is created. Relative cross-links work even when the two configured document directories have different nesting depths.

## Outputs

| Output | Meaning |
| --- | --- |
| `branch` | Branch containing the documents, including the base branch when documents are already merged |
| `pull-request-number` | Number of the created or reused PR; empty if none is needed |
| `pull-request-url` | URL of the created or reused PR; empty if none is needed |
| `spec-path` | Repository-relative specification path, when present |
| `adr-path` | Repository-relative ADR path, when present |
| `updated-issues` | Count of issue bodies updated, or that would change in a link dry run |

Skipped operations produce empty scaffold outputs and `updated-issues=0`. For example, use `${{ steps.sdlc.outputs.pull-request-url }}` after a step with `id: sdlc`.

## Tests and release preparation

From this directory, run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

The tests mock GitHub API calls. They cover scaffolding, metadata discovery, branch and PR reuse, link rewriting, configuration, actual HTTP payloads, action outputs, and running the copied package without the original repository. No GitHub credentials or network access are needed for the local suite.

The bundled [CI workflow](.github/workflows/test.yml) runs tests on Python 3.10–3.14. It also invokes the composite action with a non-merge event and read-only permissions to verify packaging without writing to GitHub. Runtime tests are not executed in consuming repositories on every action invocation; the action repository owns its tests and releases.

Copy the entire contents of this directory, including `action.yml`, `scripts/`, `tests/`, `examples/`, `.github/`, and the license, into the new repository root. There are no imports or template paths referring back to this q8s repository. Run the tests there, verify the examples against a disposable consuming repository, and publish the release. Maintain a `v1` major tag or have consumers pin a specific release or commit. Publishing to GitHub Marketplace is optional.

To migrate this repository after publishing, replace the local-script workflows with caller workflows referencing the action, keeping its existing label and directory defaults. Remove the old local scripts, templates, and their dedicated test workflow only after the versioned action is adopted. Keep existing document metadata and PR markers so ongoing work can still be found.

## Scope

The action creates templates; contributors write requirements and design rationale, implement changes, review, and merge. Removing labels does not delete documents. Adding the second label links the new document to its counterpart without rewriting the existing document. Link synchronization uses GitHub's closing issue associations and changes only matching document URLs in issue bodies, preserving anchors and Markdown labels. It does not update comments, validate document existence, or follow renamed paths. A non-merge closure does not update links.

Contributions and distributions use the included [Apache License 2.0](LICENSE).
