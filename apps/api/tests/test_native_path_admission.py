from __future__ import annotations

from collections.abc import Callable

import pytest

from app.cad_bridge import CadSnapshotAdmissionError
from app.cad_bridge.contracts import PathGeometry
from app.cad_bridge.geometry_admission import QuarantinedPath, admit_native_path


def native_path() -> dict[str, object]:
    return {
        "status": "native",
        "error_status": None,
        "closed": False,
        "coordinates": [[0, 0, 150], [20, 0, 151]],
        "sampled_max_deviation_units": 0.00025,
    }


def admit(path: dict[str, object]) -> PathGeometry | QuarantinedPath:
    return admit_native_path(
        path,
        geometry_id="path/10/C1",
        identity={"handle": "C1", "instance_chain": ["10"]},
        metres_per_unit=1,
        requested_tolerance_m=0.001,
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda path: path.update(status="unresolved", closed=None), "unresolved"),
        (lambda path: path.update(closed=None, coordinates=[]), "closure state"),
        (lambda path: path.update(coordinates=[]), "too few coordinates"),
        (
            lambda path: path.update(coordinates=[[0, 0, 0], [0, 0, 0]]),
            "equal open endpoints",
        ),
        (
            lambda path: path.update(coordinates=[[0, 0, 0], [20, 0, float("nan")]]),
            "invalid coordinates",
        ),
        (
            lambda path: path.update(sampled_max_deviation_units=-0.1),
            "invalid deviation",
        ),
        (
            lambda path: path.update(sampled_max_deviation_units=0.002),
            "exceeds requested sampling tolerance",
        ),
    ],
)
def test_path_rejection_keeps_the_first_actionable_error(
    mutation: Callable[[dict[str, object]], None], message: str
) -> None:
    path = native_path()
    mutation(path)
    with pytest.raises(CadSnapshotAdmissionError, match=message):
        admit(path)


@pytest.mark.parametrize("closed", [False, True])
def test_degenerate_path_is_quarantined_before_sampling_metadata(closed: bool) -> None:
    path = native_path()
    path["closed"] = closed
    path["sampled_max_deviation_units"] = "unusable"
    path["coordinates"] = (
        [[0, 0, 150], [20, 0, 151], [10, 0, 151], [0, 0, 150]]
        if closed
        else [[0, 0, 150], [0, 0, 151]]
    )
    result = admit(path)
    assert isinstance(result, QuarantinedPath)
    assert ("XY area" if closed else "XY length") in result.reason


def test_units_and_native_height_are_preserved_during_path_admission() -> None:
    path = native_path()
    path["sampled_max_deviation_units"] = 0.25
    result = admit_native_path(
        path,
        geometry_id="path/10/C1",
        identity={"handle": "C1", "instance_chain": ["10"]},
        metres_per_unit=0.001,
        requested_tolerance_m=0.001,
    )
    assert isinstance(result, PathGeometry)
    assert result.coordinates == [(0, 0, 150), (20, 0, 151)]
    assert result.achieved_tolerance_m == 0.00025
    assert result.identity.handle == "C1"
    assert result.identity.instance_chain == ["10"]
