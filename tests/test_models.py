from specreview.models import Analysis, Link, MapperOutput, Range, Step


def test_step_id_and_json_roundtrip():
    s = Step(spec="S", anchor="alg", number="2.1", text="t", parent="S#alg:2")
    assert s.id == "S#alg:2.1"
    assert s.model_dump()["id"] == "S#alg:2.1"


def test_patch_digest_ignores_id(patch):
    other = patch.model_copy(update={"id": "other"})
    assert patch.digest() == other.digest()


def test_mapper_output_schema_has_links():
    schema = MapperOutput.model_json_schema()
    assert "links" in schema["properties"]


def test_analysis_steps(analysis):
    assert [s.number for s in analysis.steps()] == ["1", "2", "2.1", "3"]
    assert isinstance(analysis.links[0], Link)
    assert isinstance(analysis.links[0].ranges[0], Range)
    assert isinstance(analysis, Analysis)


def test_patch_digest_ignores_source(patch):
    assert patch.digest() == patch.model_copy(update={"source": "D1"}).digest()


def test_compliance_result_schema():
    from specreview.models import ComplianceResult

    props = ComplianceResult.model_json_schema()["properties"]
    assert {"step_id", "verdict", "summary", "issues", "evidence", "context_used"} <= set(props)
