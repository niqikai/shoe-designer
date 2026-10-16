# ShoeSpec v0.1 — Milestone 1

`input/specs/shoe_spec.json` is the explicit parameter input for `slip_on_01`.
The public API is `from shoe_poc.schemas import ShoeSpec` followed by
`spec = ShoeSpec.load(path)`. Loading validates JSON, then resolves `last.file`
and `manufacturing.rules_profile.file` relative to the spec's directory.
An absolute path remains absolute. CAD import and asset existence are checked by
the last loader, rather than by the parameter schema.

All nested objects use Pydantic v2 `strict=True`, `extra="forbid"` and finite
numbers. Numeric JSON values may be integers or decimals; numeric strings and
booleans are rejected as lengths. Required fields cannot silently disappear.
The schema accepts only millimetres, the v0.1 protocol, and the implemented
`slip_on_01` template. Unknown fields, including misspelled parameter names,
raise validation errors.

## Coordinates and uncertainty

The desired convention is X from heel to toe, Y across shoe width, Z upward.
The input transform is explicit:

`p_design_mm = rotation @ p_source_mm + translation_mm`.

`rotation` contains three rows of three numbers. Its rows must be orthonormal
within 1e-6 and its determinant must be +1. Reflections, scale and shear are
rejected. `translation_mm` contains three finite components in ±10000 mm.
The normalizer can additionally position the transformed bounding box; its
metadata must record the complete applied transform, including that positioning.

`orientation_confirmed=false` preserves uncertainty about the physical heel,
toe and sole directions. An identity matrix records the chosen input transform;
it does not establish anatomical orientation. `last.side="unknown"` and
`shoe.size=null` are valid and appear in the example. Size is never inferred
from bounding-box length. `source` and `license_status` preserve provenance and
the pending purchase-license check for this private Phase 0 test asset.

## Parameter bounds

These are conservative input limits for this experimental template, not
published manufacturing specifications or guarantees of compatibility with
arbitrary lasts.

| Parameter | Units | Accepted range / meaning |
| --- | --- | --- |
| `upper.thickness_mm` | mm | 0.8–8 |
| `upper.clearance_mm` | mm | 0–5 cavity clearance |
| `upper.opening_parameters.collar_height_mm` | mm | 10–100 above normalized last base |
| `opening_start_x_fraction` | dimensionless | 0–0.8 of last X length |
| `opening_end_x_fraction` | dimensionless | >0–1; at least 0.1 after start |
| `upper.opening_parameters.transition_mm` | mm | >0–60; optional, default 60; longitudinal collar transition (experimental; 15 mm retained a steep lip on the source last) |
| `upper.opening_parameters.collar_style` | — | `quintic_roof` (legacy default) or `sculpted_3d` |
| `upper.opening_parameters.heel_lift_mm` | mm | 0–15, default 0; sculpted collar only |
| `upper.opening_parameters.side_dip_mm` | mm | 0–15, default 0; sculpted collar only; retain at least 10 mm base height |
| `upper.pattern_parameters.type` | — | `none`; continuous upper in Milestone 1 |
| `sole.height_mm` | mm | 5–60 |
| `sole.profile_parameters.margin_mm` | mm | 1–20; at least thickness + clearance |
| `sole.profile_parameters.sidewall_inset_mm` | mm | 0–10; optional, default 2; less than sole height |
| `sole.profile_parameters.toe_rise_mm` | mm | 0–20; optional, default 6 |
| `sole.profile_parameters.heel_rise_mm` | mm | 0–15; optional, default 2 |
| `sole.profile_parameters.edge_radius_mm` | mm | 0–10; optional, default 2; at most half sole height |
| `sole.profile_parameters.toe_start_fraction` | dimensionless | 0.4–0.85, default 0.68; start of forefoot rocker |
| `sole.profile_parameters.sidewall_bulge_mm` | mm | 0–5, default 0; outward radial control for the curved lower wall |
| `sole.profile_parameters.sidewall_waist_mm` | mm | 0–3, default 0; independent inward middle-wall displacement, with unchanged endpoint positions and first derivatives |
| `sole.profile_parameters.toe_roll_radius_mm` | mm | 0–15, default 0 (legacy rocker); positive values control the vertical endpoint curvature of a smooth quintic toe roll. The actual nose is its trimmed intersection with the curved wall, followed by the configured edge round; it is not a constant-radius arc. Experimental design value, not a manufacturing or footwear standard. |
| `lattice.period_mm` | mm | 5–50 |
| `lattice.threshold` | dimensionless | 0.05–1.5; not an actual wall thickness |
| `lattice.resolution_mm` | mm | 0.2–5; at most period / 4 |

The opening fractions locate the longitudinal extent of the opening cutter.
They do not represent a shoe size or a guarantee of fit. Parameters may still
be geometrically incompatible with a particular last after schema validation;
the CAD pipeline must diagnose those cases.

The repair adds optional profile fields without changing `schema_version="0.1"`.
Legacy JSON loads the stated defaults, and saving the validated model writes
those defaults explicitly. Numeric strings, booleans and nonfinite values are
rejected for the new fields by the same strict rules as existing lengths.
The radius bound leaves room for both sole edges; the inset/height bound avoids
a sidewall taper that collapses its depth. Toe and heel rise describe upward
profile changes and are not arbitrarily limited to the nominal flat sole height.
ShoeSpec validation cannot establish that a particular last admits a chosen
transition, taper, radius or offset; actual CAD construction must check this.

Lattice values reserve the required protocol section. They are validated but
are **not used to generate lattice geometry in Milestone 1**. Upper ventilation,
lattice generation and their manufacturing validation belong to later
milestones, following Milestone 1 acceptance.

## M2 design envelope

M2 additionally accepts `lattice.domain.mode`, default `planar` for old configs.
The repaired example selects `curved_lower_foot`, using the immutable M1 rocker
and lower-foot connection guide to define a piecewise linear upper roof. The
existing nonpositive `max_z_offset_mm` reduces that local roof in curved mode;
`clearance_mm` and optional reducing `bounds_mm` keep their existing meanings.
The roof, source geometry-report hash and interpolation contract are recorded
in domain metadata and must match exactly when reusing CAD certificates. This
design subset follows raised toe material without changing any accepted M1
parameter or asserting a globally uniform shell thickness.

The optional `lattice.domain.refinement` object is valid only in
`curved_lower_foot` mode. Its defaults are `toe_start_fraction: 0.925`,
`coverage_tolerance_mm: 0.1`, and `max_queries: 16000`; the completion example
explicitly budgets 30000 queries. Old specs default to `refinement: null`.
The tolerance concerns completeness depth at grid nodes, not allowed intrusion:
every allowed node in the declared toe corridor deeper than physical clearance
plus a full voxel diagonal, numerical guard and this tolerance must acquire a
negative certificate. Clearance is never reduced to fill a thin tip. Exhausted
query budgets fail the build. The explicit query journal under `debug/` is a
hash-bound incomplete seed for mandatory resumed traversal, never an accepted
completed-domain cache. Independent validation additionally requires expected
terminal material to reach the principal connected exported STL component.

## Experimental manufacturing profiles

`manufacturing.process` is `SLS`. `material_profile` declares a profile ID,
material `TPU`, and status `EXPERIMENTAL`. `rules_profile` declares an ID,
external JSON file and status `EXPERIMENTAL`. The schema does not accept a
production or passing status for these unvalidated profiles. Validation of
these declarations does not constitute a manufacturing check, a physical
printing test, or a wearability assessment.

To save a validated spec use `spec.model_dump_json(indent=2)`. A spec loaded
with `ShoeSpec.load` contains resolved absolute asset/profile paths so that a
saved copy can be loaded from an output directory without changing its meaning.

## Independent surface measurements

`shoe_poc.validation.surface.validate_surface_samples(last_shape, build, spec)`
returns a JSON-serializable report using the actual normalized CAD last and final
shoe, with `build.opening`, `build.cavity` and `build.sole` as actual CAD bodies.
It does not read generator section offsets or stored overlap values to determine
whether dimensions pass. UV points are classified against face trims, evaluated
on the exact last surfaces, and probed outward along CAD face normals. OCCT line
intersections and solid classification identify the first material interval in
the final shoe. That interval gives normal-direction clearance and directional
wall thickness at the sampled site.

The numerical M1 fit targets are `upper.clearance_mm ± 0.25 mm` and
`upper.thickness_mm ± 0.35 mm`. These are experimental geometry tolerances, not
manufacturing specifications. For the legacy example they mean 1.0 mm clearance
and 2.5 mm directional thickness. A transverse-only offset may violate these
normal-direction targets on steep or concave regions; such deviations must be
reported, rather than replaced by nominal parameter values.

At most 512 deterministic sites are selected. The report records sample points,
normals, material intervals, signed deviations, observed minimum/maximum/mean,
maximum absolute error, face coverage and unmeasurable regions. Sites in or near
the actual opening cutter and at the sole connection are explicitly excluded.
Sole exclusion additionally uses geometric distance from the measured material
interval to the actual CAD sole, allowing a raised sole junction without hiding
the upper behind a fixed elevated cutoff. The actual opening/cavity common
volume, final-shoe/opening common volume and final-shoe/last common volume are
checked independently. A failed measured target or blocked opening gives
`geometry_status="FAIL"`; unmeasurable planned sites or incomplete face coverage
give `PARTIAL`, even when all measurable values satisfy their targets.

`geometry_status="PASS"` applies only to executed sampled checks. The overall
surface report remains `PARTIAL`, and global minimum wall thickness, global
minimum clearance, global self-intersections, physical fit, visual acceptance
and manufacturing qualification remain `NOT_VERIFIED`. Face counts and planar
surface area fractions are geometry descriptors; they do not establish a smooth
appearance or replace the user's review of the generated previews.
