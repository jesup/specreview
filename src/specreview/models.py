import hashlib
from typing import Literal

from pydantic import BaseModel, Field, computed_field

LinkKind = Literal["implements", "partial", "structural", "deviates"]
UnmappedReason = Literal["plumbing", "error-path", "test", "unexplained"]
Side = Literal["new", "old"]


class DiffLine(BaseModel):
    kind: Literal["add", "del", "ctx"]
    old: int | None
    new: int | None
    text: str


class Hunk(BaseModel):
    header: str
    lines: list[DiffLine]


class FileDiff(BaseModel):
    path: str
    hunks: list[Hunk]


class Patch(BaseModel):
    id: str
    parent_ids: list[str] = Field(default_factory=list)
    description: str = ""
    source: str = ""
    files: list[FileDiff]

    def digest(self) -> str:
        data = self.model_dump_json(exclude={"id", "parent_ids", "source"})
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def file(self, path: str) -> FileDiff | None:
        return next((f for f in self.files if f.path == path), None)


class Step(BaseModel):
    spec: str
    anchor: str
    number: str
    text: str
    parent: str | None = None
    title: str = ""
    target: str | None = None
    depth: int = 0
    group: str = ""

    @computed_field
    @property
    def id(self) -> str:
        return f"{self.spec}#{self.anchor}:{self.number}"


class SpecDoc(BaseModel):
    ref: str
    sha: str
    title: str
    url: str
    anchor: str = ""
    steps: list[Step]


class Range(BaseModel):
    file: str
    side: Side = "new"
    start: int
    end: int


class Link(BaseModel):
    patch_id: str = ""
    step_id: str
    ranges: list[Range]
    kind: LinkKind
    confidence: float
    rationale: str


class Unmapped(BaseModel):
    patch_id: str = ""
    ranges: list[Range]
    reason: UnmappedReason
    note: str


class Finding(BaseModel):
    patch_id: str = ""
    text: str
    severity: Literal["info", "warn", "error"]
    step_id: str | None = None
    range: Range | None = None


class ComplianceIssue(BaseModel):
    text: str
    severity: Literal["info", "warn", "error"]
    file: str | None = None
    line: int | None = None


class ComplianceEvidence(BaseModel):
    file: str
    line: int
    note: str


class ComplianceResult(BaseModel):
    step_id: str
    verdict: Literal["compliant", "partial", "non-compliant", "unclear"]
    summary: str
    issues: list[ComplianceIssue] = Field(default_factory=list)
    evidence: list[ComplianceEvidence] = Field(default_factory=list)
    context_used: list[str] = Field(default_factory=list)


class MapperOutput(BaseModel):
    links: list[Link]
    unmapped: list[Unmapped]
    findings: list[Finding]


class Analysis(BaseModel):
    patch_id: str
    docs: list[SpecDoc]
    links: list[Link] = Field(default_factory=list)
    unmapped: list[Unmapped] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str = ""
    cached: bool = False

    def steps(self) -> list[Step]:
        return [s for d in self.docs for s in d.steps]
