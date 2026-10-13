# Milestone 1 geometry and shape repair

`LastLoader` imports the configured licensed STEP, records its SHA-256, original header and units, and requires one valid B-Rep solid. The malformed `FILE_NAME` metadata is repaired only in a temporary copy; the original file and `DATA` section stay unchanged. `normalize_last` applies the explicitly configured proper rigid transform. The sample orientation remains provisional; foot side and shoe size are not inferred.

## Selected envelope method

The repair selects `local-exact-cad-offset-v3`, retaining the actual foot surfaces rather than reconstructing a convex envelope.

1. Derive a partition plane at 66% of the normalized last's height. It must be above the retained shoe surface; the generator checks that artificial partition geometry is removed by the configured collar.
2. Intersect the source with a clipping half-space to obtain the genuine foot B-Rep. For this asset it has 175 faces, compared with 235 on the full source. A clipping box is a cutting tool, not a replacement shoe geometry.
3. Apply OCCT `PerformByJoin` normal offsets with ARC joins, 0.00001 mm numerical tolerance, to clearance and clearance + upper thickness. Require valid single solids.
4. Extend both volumes with the original source ankle above partition minus 2 mm. This keeps the complete source in the cavity and exterior. The extension has **no offset clearance or wall-thickness qualification** and must lie in the removed opening region.
5. Require exact B-Rep last/cavity/exterior containment, with residual volume at most 0.01 mm³. Recheck source, cavity and opening exclusion after assembly.

Independent same-argument experiments establish that whole-source offsets at 1 and 3.5 mm return `IsDone=False`, while the clipped foot produces valid single offset solids and zero omitted source-foot volume in about 2 seconds per offset. The standalone repaired envelope has zero last-outside-cavity, cavity-outside-exterior and last-outside-exterior volumes. Input-specific results do not guarantee arbitrary replacement lasts work without template adaptation.

No approximation is applied to the source foot surfaces, so source reconstruction error is recorded as zero by identity, not as a sampled global offset accuracy claim. Actual clearance and directional wall thickness are measured separately against the final shoe.

## Rejected reconstruction experiments

The planned true CAD-section reconstruction was implemented and exercised. It extracts genuine plane intersection wires, preserves re-entrant contours, establishes arc-length correspondence, uses periodic splines with shared parameters and includes rounded pole closures. Three-millimetre initial stations are adaptively refined at most three times. Fit is measured on actual zero-offset OCCT solids at independent planes, rather than relying only on the SciPy interpolant.

The original last has a branching ankle cross-section, so a full-height single transverse loft is unsupported. Separate transverse-foot and horizontal-ankle patches reached sampled actual-CAD errors of 0.2147 and 0.1890 mm, respectively, below the 0.25 mm target. Nevertheless their fusion was impractically expensive, and a continuation trial was invalid. Those experiments are **not accepted generated shoes**. Their STEP checkpoints, fit measurements and failure logs remain in ignored `output/m1_repair/debug/`. `reconstructed_section_envelopes` is an explicit diagnostic and is never an automatic fallback that converts a failed production build into success.

The prior wide support-slab/convex-polygon method is no longer selected. It inflated the upper and erased local concavities; ruled lofts and copied end sections caused the faceted skin and flat end caps. The original `output/m1/` is preserved for comparison.

## Curved collar

The collar cutter uses a quintic Bezier roof from the configured rear height to the actual front-vamp surface. The zero first/second endpoint derivatives preserve the level rear collar. Its extrusion spans the true local exterior; the resulting three-dimensional rim follows the actual exterior/cutter intersection rather than a rectangular opening boundary. The template retains the configured front position with a 0.00001 mm numerical separation at the roof endpoint, avoiding an exposed vertical termination face.

Actual STL review rejected the initial 15 mm transition: its 34.86 mm rise still looked abrupt despite continuous derivatives. The experimental default is now 60 mm. The rear height and front-position parameters keep their meaning. Runtime checks extract the actual OCCT section, require one closed exterior rim and one connected cavity entry region, and bound front-position error to 0.25 mm. CAD edge endpoint tangents and native curve/vertex discrepancies are reported separately. Synthetic CAD regressions detect the former vertical-cut corners and multiple entry windows. The partition guard rejects any collar exposing the artificial cap or unqualified ankle continuation.

`upper_cavity_overlay.png` shows separately reimported component meshes and identical-plane sections against the real last. This component review precedes approval of the assembled shoe; mathematical continuity alone is insufficient for appearance acceptance.

## Sole and assembly requirements

The repaired sole must follow lower-foot contours, preserve the `last_min_z - sole.height_mm` bottom datum, and use the configured inset, toe/heel rises and edge radius. Source-derived connection and transition geometry must preserve the cavity and avoid a ledge at the upper junction. These are experimental template controls, not manufacturing specifications.

`ShoeAssembler` must require one valid positive-volume solid and independently retain the original last/cavity/opening exclusions. A valid Boolean result alone is insufficient for M1 appearance acceptance.

The selected sole is `cad-lower-foot-periodic-rocker-sole-v7`. It measures lower widths on genuine transverse CAD sections, builds cubic side contours and tangent-matched heel/toe caps, and uses that closed contour to construct one periodic three-dimensional stock. A thin contour extrusion is only a ray reference; it is not the generated sole. The stock follows three actual exterior CAD rings and their oriented normals, including the full heel/toe X component. Shared `Q` and `Q ± D/3` control rows preserve the band tangents when the longitudinal guide handles are constrained. Redundant knots are reduced only within 0.00000001 mm.

The floor cutter is an exact quintic rocker with a flat middle, heel transition ending at 22% of last length and toe transition starting at 68%. OCCT rounds the actual floor/wall intersection with the configured radius. Actual trimmed-face principal-curvature samples report the measured bottom radius separately from the configured value. Heights 20 and 25 move the lowest datum by 5 mm while the upper guide stays fixed.

The final junction checker measures oriented adjacent-face normals on actual shared edges, with a signed dot product. Exact CAD distances classify and exclude cavity/opening boundaries. Heel, middle and toe coverage, excluded locations and measurement errors remain in metadata. Its 2-degree angle limit and 0.02 mm curvature-radius tolerance are experimental M1 numerical checks, not manufacturing standards or global continuity certificates. Missing coverage or an unmeasurable normal prevents PASS. The assembler also measures any original upper material omitted by the final union. Previous loft, end-guide and native-junction-fillet failures remain in `output/m1_repair/debug/`; no mesh repair or failed-candidate fallback can supply a PASS.

## Independent evidence and API

`validate_surface_samples` uses trim-classified points and outward normals from the actual last, ray intersections with the final shoe, and material midpoint classification. It does not reuse generator rings or declare the configured thickness as a measurement. Reports retain actual sample locations, observed deviations, excluded opening/sole regions and unmeasurable locations. Finite sampling does not certify global minima or physical fit.

```python
loaded = LastLoader().load(spec.last.file)
normalized_last, normalization = normalize_last(loaded.shape, spec.last.coordinate_system)
result = ShoeAssembler().build(normalized_last, spec)
# result.shape, result.upper, result.sole, result.cavity, result.opening
```

Actual exported STL supplies the preview. Optional `--compare-with` renders the old and repaired meshes with identical cameras and scale, plus sections against the real last. No smooth shading or image editing substitutes for a geometry change.

The native STL exporter explicitly uses **absolute 0.15 mm deflection** and 0.1 rad angular deflection. Inspection of build123d 0.10 revealed that its convenience exporter instead sets the OCCT relative-deflection flag. Only cached triangulations are cleared before remeshing; the CAD surfaces and volume stay unchanged. An independent sphere regression measures tessellation sag and verifies that a previously cached fine mesh does not determine subsequent coarse output.

CAD pole tessellation can emit zero-area STL records. The exporter removes only records with exactly zero cross-product area, using no tolerance, and preserves every positive-area record byte for byte, including normals and attributes. Counts and settings are recorded in `mesh_export_report.json`. It never fills a hole, relocates a vertex or smooths geometry. Mesh validation retains Euler, winding, connectivity, degenerate-triangle and edge-incidence requirements, and also checks cyclic vertex links: a water-tight mesh can still have two sheets touching at a zero-thickness point. The real failed junction trials supplied three such points; geometry repair, rather than triangle deletion, is required.

Pipeline, sampled geometry, appearance/user acceptance, manufacturing, physical printing and wear are separate report levels. M1 remains PARTIAL while appearance acceptance is pending. Derived assets remain local and ignored by Git; source provenance and license records are maintained in the asset documentation.
