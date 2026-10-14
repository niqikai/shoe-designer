# M1 annotated appearance refinement

This round follows the user's eight annotations on the actual M1 repair STL views. Geometry qualification and appearance acceptance remain separate. Keep the normalized real last, 1 mm clearance, 2.5 mm upper wall target, the 0.01 mm³ containment/exclusion threshold, and M1 scope. Preserve `output/m1/` and `output/m1_repair/`; new candidates and final artifacts belong to `output/m1_design_refine/`.

| Annotation | Concrete design direction | Independent evidence |
| --- | --- | --- |
| 1 / 4: high, pointed vamp crest and awkward side profile | Move the collar front onto the lower actual vamp and replace the flat roof with a sculpted three-dimensional cut surface. | Actual CAD peak height, side contour and rim profiles; reimported same-camera STL comparison. |
| 5: flat, abrupt collar line | Add a continuous heel-to-side height variation and transverse side dip. | Actual closed entry rim and tangent checks, retained front/side material and connected cavity opening. |
| 3: straight, heavy sole wall | Reduce the lower footprint's extra margin and curve the sidewall between its narrower lower edge and the unchanged CAD exterior junction. | True cross-sections and width-versus-height; do not count only the small bottom fillet as sidewall shaping. |
| 6: insufficient forefoot rocker | Increase toe rise and begin the quintic rocker earlier. | Actual floor heights/slope, retained central datum, material thickness and cavity exclusion. |
| 2 / 7 / 8: heavy, wide forefoot and teardrop proportion | Remove avoidable sole expansion and rebalance the visible silhouette through the opening and rocker. | Compare last, cavity, upper and final forefoot widths; explicitly retain any proportion forced by the real last. |

Exact CAD measurements of the old envelope show approximately 78.86 mm at `0.55L`, 58.45 mm at `0.64L`, and 54.76 mm at `0.66L`. At `0.68L` the source last is approximately 100.05 mm wide and its existing required exterior is 107.08 mm wide. Substantially narrowing that upper while preserving this last, clearance and wall thickness is unsupported. Do not scale the last or cut through the cavity to make a visual metric improve.

First experimental candidate: `opening_end_x_fraction=0.64`, sculpted collar, base collar height 43 mm, heel lift 2 mm and side dip 5 mm; sole margin 4.5 mm, inset 2 mm, outward sidewall control 2 mm, toe rise 12 mm starting at `0.60L`, heel rise 2 mm and bottom edge radius 2 mm. These are editable design values, not manufacturing standards or accepted appearance targets.

The original v0.1 configuration keeps its existing geometry defaults. The new configuration is `input/specs/shoe_spec_design_refine.json`. Numerical and topology checks cannot set appearance PASS. Each annotated issue must be recorded as improved, constrained or unresolved with real geometry and images as evidence; overall M1 remains PARTIAL until the user accepts the revised appearance.
