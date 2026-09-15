# acadrust 0.5.5: active ACDS index experiment

Pinned upstream: `hakanaktt/acadrust`,
`f9e14a06e4d63b5bf6df20f31e633cab6ffba8f9` (MPL-2.0).
This is a local downstream patch, not a published fork or runtime dependency.

`0.5.5-active-acds-index.patch` makes the existing modeler attachment prefer
the active datastore index. It checks indexed segment bounds, uses full 64-bit
handles, selects live records instead of scanning stale physical records, and
rejects missing/duplicate owners and unsupported paged payloads. For an
unrecognized datastore signature the upstream legacy path remains; this patch
does not claim universal strictness outside the recognized v2 format.

Six new synthetic regression tests cover stale records, reordered records,
64-bit handles, missing/duplicate owners, corrupt bounds and paged payloads.
The existing two ACDS library tests also pass. Full official Parkovaya strict
read/write passes; the output is byte-identical to unpatched acadrust for the
same root and locked probe dependencies. Its upstream result was already
correct for those four bodies; the patch adds stronger ownership guarantees.

Reproduce in a separate checkout at the pinned revision:

```
git apply /absolute/path/to/0.5.5-active-acds-index.patch
cargo test --release --lib acds -- --test-threads=1
```

On Windows source the project's `scripts/windows-env.ps1`, and set Cargo home
and target on D before building. Use `scripts/cad-lab/acadrust_probe.rs` as a
separate binary with a path dependency on that checkout. The probe lockfile
and the SDK's test lockfile are distinct; do not confuse a compiler/dependency
change with a converter performance improvement.

Fork adoption still requires full object/attribute fidelity, all XREFs,
writer roundtrips, old fixtures and Linux verification. Do not combine partial
SDK outputs into a supposedly complete project based only on matching counts.
