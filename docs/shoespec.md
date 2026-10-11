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
| `upper.pattern_parameters.type` | — | `none`; continuous upper in Milestone 1 |
| `sole.height_mm` | mm | 5–60 |
| `sole.profile_parameters.margin_mm` | mm | 1–20; at least thickness + clearance |
| `lattice.period_mm` | mm | 5–50 |
| `lattice.threshold` | dimensionless | 0.05–1.5; not an actual wall thickness |
| `lattice.resolution_mm` | mm | 0.2–5; at most period / 4 |

The opening fractions locate the longitudinal extent of the opening cutter.
They do not represent a shoe size or a guarantee of fit. Parameters may still
be geometrically incompatible with a particular last after schema validation;
the CAD pipeline must diagnose those cases.

Lattice values reserve the required protocol section. They are validated but
are **not used to generate lattice geometry in Milestone 1**. Upper ventilation,
lattice generation and their manufacturing validation belong to later
milestones, following Milestone 1 acceptance.

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
