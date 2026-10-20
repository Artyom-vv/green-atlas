"""Candidate ordering and accepted-space pruning, without CAD or persistence."""

from app.planning.changes.contracts import PlanObjectAddOperation
from app.planning.contracts import PlanObjectCreate
from app.planning.search.candidate_queue import CandidateQueue


def operations(*xs: float) -> list[PlanObjectAddOperation]:
    return [
        PlanObjectAddOperation(object=PlanObjectCreate(kind="tree", x=x, y=0))
        for x in xs
    ]


def test_deferred_neighbour_remains_available_after_an_unaccepted_probe() -> None:
    queue = CandidateQueue(operations(0, 1, 10), spacing_m=3)

    assert queue.batch(2) == [0, 2]
    assert queue
    assert queue.batch(2) == [1]
    assert not queue


def test_accepted_space_prunes_only_remaining_points_inside_the_step() -> None:
    queue = CandidateQueue(operations(0, 1, 3, 10), spacing_m=3)

    assert queue.batch(1) == [0]
    assert queue.accept(0, 0) == 1
    assert queue.accept(0, 0) == 0
    # The exact spacing boundary remains eligible; distant space comes first.
    assert queue.batch(2) == [3, 2]
    assert not queue


def test_equal_clearance_keeps_original_candidate_order() -> None:
    queue = CandidateQueue(operations(0, 10, -10), spacing_m=1)

    assert queue.batch(1) == [0]
    assert queue.accept(0, 0) == 0
    assert queue.batch(1) == [1]
    assert queue.batch(1) == [2]


def test_queue_preserves_operation_coordinates_and_input_objects() -> None:
    candidates = operations(0.123456789, 10.987654321)
    before = [candidate.model_dump() for candidate in candidates]
    queue = CandidateQueue(candidates, spacing_m=1)

    assert queue.points.tolist() == [[0.123456789, 0.0], [10.987654321, 0.0]]
    assert queue.batch(1) == [0]
    queue.accept(0.123456789, 0)
    assert queue.batch(1) == [1]
    assert [candidate.model_dump() for candidate in candidates] == before


def test_empty_queue_has_no_batch() -> None:
    queue = CandidateQueue([], spacing_m=3)

    assert not queue
    assert queue.batch(10) == []


def test_zero_batch_does_not_consume_candidates() -> None:
    queue = CandidateQueue(operations(0, 10), spacing_m=3)

    assert queue.batch(0) == []
    assert queue.batch(2) == [0, 1]
