# Milestone 1 geometry

`LastLoader` reads the configured local STEP file, records its SHA-256, source length-unit tokens and original `FILE_NAME` record, and requires one valid B-Rep solid. The supplied test asset has a nonstandard unquoted license notice in `FILE_NAME`. The loader replaces that metadata record with syntactically valid STEP metadata in a temporary file and preserves the original record in the report. The `DATA` section remains unchanged. The temporary file is deleted after import; the purchased source is never rewritten.

`analyze_shape` measures solids, faces, shells, edges, vertices, bounding box, area and volume, checks OCCT topology validity and closed shells, and reports dimensions in millimetres. It does not infer shoe size, foot side, or toe direction. `normalize_last` applies the configured proper orthonormal rotation and translation with OCCT's rigid transformation API, and records a 4×4 matrix using column vectors. The sample configuration leaves orientation unconfirmed and side unknown. Its proposed +X toe / +Z up convention follows inspection of the source's low forefoot and high heel/ankle regions; the datums remain explicit configuration. User confirmation is still required.

## Why the upper uses section reconstruction

Direct OCCT offsets of the actual test asset were attempted with ARC and INTERSECTION joins. Both failed at the trial distances. The implemented fallback reconstructs the actual last's transverse sections; it does not replace the last with a box or ellipsoid.

1. Tessellate the real normalized last at 0.25 mm linear deflection.
2. Collect triangles overlapping 36 transverse support slabs normal to X. Each slab extends slightly more than one section spacing on each side, so adjacent profiles cover their intervening geometry.
3. Compute each slab's 48 uniformly spaced YZ support directions.
4. Shift support lines by clearance plus a 0.6 mm reconstruction allowance, then intersect adjacent lines to form a **circumscribed** convex polygon. Repeat at clearance + allowance + nominal upper thickness. Fixed supporting directions keep profile correspondence deterministic.
5. Create nested, closed, ruled B-Rep lofts, including end caps extending past the last ends.
6. Cut the inner envelope and an open-to-exterior collar tool from the outer envelope.
7. Subtract the exact imported last again from the final assembly as a hard exclusion constraint.

The nested envelopes provide a controllable transverse offset difference. Convexification bridges local concavities, and the ruled result is visibly faceted. The 0.6 mm allowance is an experimental reconstruction parameter, not a certified error bound. Exact B-Rep containment checks require that the entire imported last is inside the cavity envelope, the entire cavity is inside the outer envelope, and the entire last is inside the outer envelope. A failed containment check stops generation. Nominal section offset is **not** proof of minimum global wall thickness or certified fit clearance. Both require further measurement. A previous experimental inscribed-ray envelope failed these containment checks and produced unintended toe/side holes; it was rejected. The implementation uses circumscribed support polygons to prevent that failure. The exact-last Boolean exclusion independently proves the model has not filled the original last volume.

The collar uses a vertical cutting prism defined by the configured X fractions and height above the normalized last's minimum Z. A zero start fraction extends the cut beyond the heel extremum. This creates a low rear collar and an open entry into the cavity. Physical foot insertion, collar stretch and comfort have not been verified.

## Sole and assembly

`SoleGenerator` derives the sole outline from the convex hull of the real last's sampled XY projection, offsets that outline by the configured margin, and extrudes it. The sole bottom is `last_min_z - sole.height_mm`; thus height is the nominal distance below the lowest last point. Stock overlaps the last base by up to 18 mm to connect the rising heel and forefoot. Cavity subtraction creates the internal contoured footbed. The convex support outline deliberately bridges the medial arch and is a documented template simplification.

`ShoeAssembler` fuses the outer envelope and sole stock before cutting the inner cavity, exact last and entry tool. It rejects invalid geometry, multiple solids, nonpositive volume, a missing entry cut, or residual intersection volume above 0.01 mm³ with the last, cavity or opening. A CAD intersection with a transverse Y line at the forefoot additionally measures two positive sidewall material intervals; this proves nonzero local directional thickness only. Individual upper, sole, cavity and opening shapes remain available to callers for diagnostics. Root CLI validation separately handles STEP/STL round trips and mesh integrity.

Changing `sole.height_mm` moves the sole bottom and changes the actual extruded solid. It does not alter the last or the upper. The integration test compares distinct resulting volumes and extents.

## API

```python
loaded = LastLoader().load(spec.last.file)
normalized_last, normalization = normalize_last(loaded.shape, spec.last.coordinate_system)
last_metadata = analyze_shape(normalized_last)
result = ShoeAssembler().build(normalized_last, spec)
# result.shape, result.upper, result.sole, result.cavity, result.opening
# result.metadata contains algorithm, approximation, exclusion and timing evidence.
```

Source assets and all derived STEP/STL previews stay in ignored local directories. The test asset carries a CC BY-NC-ND 4.0 notice; purchase rights require review before redistribution or commercial use. This POC reports geometry only and does not claim manufacturing, physical printing or wear validation.
