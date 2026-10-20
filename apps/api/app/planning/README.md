# Planning

The planning applications propose positions and evaluate changes against an
existing project. They do not read CAD files or publish prepared geometry.
Every manual or automatic candidate uses `PlanEvaluation` and the same geometry
port. Applying a preview belongs to the existing change-set application.

`candidate_search.py` receives the existing `Project`, add-operation and
evaluation contracts. A project must already have a plan. The search copies its
object list and records accepted candidates only in that working plan.
`candidate_queue.py` owns probe order and spacing pruning using NumPy arrays.
Deferring a nearby probe within a batch does not reserve space. Only accepting a
position prunes its remaining neighbourhood; the exact spacing boundary stays
eligible. Rejected or unverified probes leave neighbours available.

The bounded free-layout search reads `candidate_search` from the versioned
placement configuration. Regular fills and masks use the separate alternative
probe constants in `planning/config.py`. Those technical limits do not change
normative setbacks, the geometry evaluator, coordinate rounding or random seeds.

`sampling.py` owns deterministic Poisson proposals and sparse sampling windows.
Its named attempt and stagnation budgets retain the existing values and seed
stream. Alternative probes include disconnected pockets without reserving space;
only the application evaluator can accept and reserve a planting position.
An empty search area returns no proposals before creating the random generator.
Fully occupied building bands therefore do not spend the sampling attempt budget;
nonempty areas retain their existing coordinate stream.
`patterns.py` dispatches the existing typed requests and retains its sampler alias
for current consumers. Geometry and collection types describe the existing
Shapely adapter boundary, without replacing GeoJSON admission or safety checks.

`building_patterns.py` owns facade contours and grouped proposals near buildings.
It receives the existing safe search area and guide features; it does not compute
growth clearances, evaluate safety or apply a preview. Shared cluster offsets and
the bounded proposal list belong to `sampling.py`, so the facade module does not
depend on the dispatcher. Existing generator names remain available from
`patterns.py`. The generated-coordinate limit in `config.py` stays separate from
the alternative-probe budget. Facade search offsets, band width and orientation
heuristics are named locally with their existing values and arithmetic order.

Contract checks are `test_candidate_queue.py` and
`test_candidate_search_policy.py`. The existing candidate-search, planning,
allocation, sparse-sampling, regular-grid and row-quantity tests cover accepted
space, partial results, disconnected zones and deterministic coordinates. Ruff
and mypy explicitly include the search, queue, generators, sampler and changed
tests. The compact sampler test fixes the full ordered seeded result; preparation
and alternative-probe tests cover sparse areas and unreserved candidates.
`test_building_patterns.py` fixes full contour/group coordinates and group
identities, existing clearance exclusion, output-limit errors and import aliases.
`test_building_screen.py` continues to check growth, selected zones and preview
without writing the plan.
