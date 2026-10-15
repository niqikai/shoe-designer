# Milestone 1 architecture

## Scope

The active vertical slice is Milestone 1 only: inspect a locally supplied STEP shoe last, normalize it using an explicit configured transform, validate a versioned ShoeSpec, generate a parametric base shoe, and export STEP and STL when the geometry operations succeed. The CLI is headless. A command's presence in documentation is not evidence that it is implemented or has run successfully.

Milestone 2 lattice generation, Milestone 3 whole-shoe manufacturing checks and additional export formats, and Milestone 4 Meshy/design mapping are outside the current runtime scope. In particular, `run` must remain equivalent to `build-base` until a later milestone is explicitly started and accepted.

## Component boundaries

- **CLI**: parses milestone commands, paths, and options; returns meaningful process status and diagnostics.
- **Last handling**: imports STEP, inspects available geometry properties, applies the configured coordinate transform, and writes metadata plus normalized STEP. Shoe-tip direction, side, and ground reference must be explicit when geometry alone cannot determine them reliably.
- **ShoeSpec**: versioned JSON contract. Lengths use millimetres and are range-checked before geometry construction.
- **Geometry**: separate upper, sole, and assembly responsibilities. The shoe last is an input constraint, never a hard-coded production asset. The base shoe must preserve a cavity and opening; an overall scaled last is not a finished shoe.
- **Export and validation**: write base STEP/STL and inspect exported files only where implemented. Report each geometry assertion and export status from actual results.
- **Manufacturing rules**: explicitly experimental configuration. It is not a validated process window or a printability certification.

## Data flow

```text
local STEP + ShoeSpec JSON
          ↓
  inspect and normalize last
          ↓
  construct base upper and sole
          ↓
  assemble and export STEP / STL
          ↓
  report actual checks and artifacts
```

The original STEP remains local. Neither the original asset nor geometry derived from it may be uploaded to Meshy or another third party. Meshy credentials are not needed for Milestone 1.

## Current CLI contract

The planned interface is `inspect-last --input ... --output output/m1`, `build-base --spec ... --output output/m1`, `run --spec ... --output output/m1`, and `validate --model ...`. The latter validates the supplied model only to the extent of checks actually implemented. `run` is Milestone 1 only. Consult CLI help and implementation before relying on options or behavior.
