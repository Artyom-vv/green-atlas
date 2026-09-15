# ezdxf 1.4.4: SAB vector transforms

`1.4.4-sab-vector-transform.patch` extends the existing SAB matrix loader to
accept four typed vectors, in addition to the existing literal string form.
Uses the existing `read_vec3`; adds no tokenizer, CAD parser, or curve sampler.
Upstream and this downstream modification: MIT.

The exact patched module is tested only inside a disposable Python process by
`scripts/cad-lab/verify_ezdxf_sab_patch.py`. Installed ezdxf and the application
runtime have not been changed. The source version is 1.4.4; do not apply this
patch to an arbitrary future release without checking the hunk and tests.

Verified on all four original Parkovaya SAB payloads, the old literal matrix,
a typed matrix including scale/reflection/translation, and two invalid inputs.
The source bytes stay intact. This fixes loading of transforms, **not** the
missing ellipse-curve implementation or full curved-face extraction.
