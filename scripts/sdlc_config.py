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

"""Configuration shared by both action operations."""

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def document_directory(value):
    value = value.rstrip("/")
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or path.is_absolute()
        or path.as_posix() != value
        or ".." in path.parts
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(
            "Document directories must be normalized repository-relative paths"
        )
    return value


@dataclass(frozen=True)
class Config:
    base_branch: str = "main"
    specs_directory: str = "doc/specs"
    adrs_directory: str = "doc/adrs"
    spec_label: str = "needs-spec"
    adr_label: str = "needs-adr"
    templates_directory: Path | None = None

    def __post_init__(self):
        for field in ("specs_directory", "adrs_directory"):
            object.__setattr__(self, field, document_directory(getattr(self, field)))
        specs = PurePosixPath(self.specs_directory)
        adrs = PurePosixPath(self.adrs_directory)
        if specs == adrs or specs in adrs.parents or adrs in specs.parents:
            raise ValueError("Specification and ADR directories must not overlap")
        if not self.spec_label.strip() or not self.adr_label.strip():
            raise ValueError("Document labels must not be empty")
        if self.spec_label == self.adr_label:
            raise ValueError("Specification and ADR labels must differ")
        if not self.base_branch.strip() or any(
            ord(char) < 32 for char in self.base_branch
        ):
            raise ValueError(
                "Base branch must be nonempty and contain no control characters"
            )

    @property
    def labels(self):
        return {self.spec_label: "spec", self.adr_label: "adr"}

    def directory(self, kind):
        return self.specs_directory if kind == "spec" else self.adrs_directory

    def document_pattern(self):
        return (
            rf"(?:{re.escape(self.adrs_directory)}/\d+-|"
            rf"{re.escape(self.specs_directory)}/)[^/]+\.md"
        )

    def template_path(self, kind, bundled):
        if self.templates_directory is not None:
            candidate = self.templates_directory / f"{kind}.md"
            if candidate.is_file():
                return candidate
        return bundled / f"{kind}.md"
