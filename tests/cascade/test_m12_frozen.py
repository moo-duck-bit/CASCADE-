import pytest

from document_ai.cascade import freeze


def test_snapshot_covers_prompts_rules_spec():
    s = freeze.snapshot()
    assert {"domain_packs/ec_sw/rules_table.yaml", "domain_packs/ec_sw/pack.yaml", "verifier_spec.md"} <= set(s["files"])
    assert any(f.startswith("prompts/locator_") for f in s["files"])
    assert any(f.startswith("prompts/editor_") for f in s["files"])
    assert s["presets"]["full"] == s["config_hash"]


@pytest.mark.skipif(not freeze.FROZEN.exists(), reason="not frozen yet (P13 뒤 v3 고정)")
def test_frozen_state_unchanged():
    assert freeze.check() == []
