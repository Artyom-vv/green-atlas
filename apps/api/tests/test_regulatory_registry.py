from app.regulations.registry import REGISTRY_REVISION, applied_record_ids, registry_snapshot


def test_registry_keeps_process_basis_separate_from_machine_checked_rules() -> None:
    snapshot = registry_snapshot(["pp743-3.6.3-building"])

    assert snapshot["revision"] == REGISTRY_REVISION
    assert snapshot["applied_rule_ids"] == [
        "pp1160-permit-service",
        "pp616-compensation-process",
        "pp743-3.6.3-building",
    ]
    records = {item["id"]: item for item in snapshot["records"]}
    assert records["pp743-3.6.3-building"]["coverage"] == "implemented"
    assert records["pp743-3.6.3-building"]["machine_checkable"] is True
    assert records["pp616-compensation-process"]["coverage"] == "partial"
    assert records["pp616-compensation-process"]["machine_checkable"] is False
    assert records["pp616-compensation-process"]["release_gate_machine_checkable"] is True
    assert records["pp1160-permit-service"]["coverage"] == "partial"
    assert records["pp1160-permit-service"]["machine_checkable"] is False


def test_unknown_issue_rule_is_not_silently_declared_as_applied_regulation() -> None:
    assert applied_record_ids(["unknown-rule"]) == ["pp1160-permit-service", "pp616-compensation-process"]
