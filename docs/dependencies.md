# Runtime dependencies and licenses

The verified local stack is Python 3.11.17, build123d 0.10.0 and cadquery-ocp 7.8.1.1.post1. `uv.lock` records all selected versions and `requirements.lock` is its hashed export for the Dockerfile. `ocp-gordon` is pinned to 0.1.17 because release 0.3.1 requires OCP 8 APIs and failed import with this OCP 7.8 stack. No library source was patched.

Core tools support command-line operation without GUI modeling. Installed metadata identifies build123d/ocp-gordon as Apache-2.0, Trimesh/Pydantic as MIT, NumPy/SciPy/NetworkX as BSD-family, and Matplotlib under its PSF-derived license. This is a core dependency inventory, not a complete binary distribution audit of every bundled component.

The [OCP binding](https://github.com/CadQuery/OCP/blob/master/LICENSE) is Apache-2.0. Its underlying **Open CASCADE Technology** kernel is **LGPL-2.1 with the OCCT exception**; this POC makes use of facilities provided by OCCT. See the [OCCT 7.8.1 exception](https://github.com/Open-Cascade-SAS/OCCT/blob/V7_8_1/OCCT_LGPL_EXCEPTION.txt) and its LGPL license. Distributing kernel binaries requires retaining applicable notices and fulfilling LGPL requirements, including source availability for the library and any modifications. Review the shipped wheel/container license materials when preparing a distribution. No GPL-only CAD application or commercial CAD runtime is required by this milestone.

The purchased STEP asset has separate restrictions. The code dependencies' licenses do not authorize reuse of that shoe last; see [asset provenance](asset_provenance.md). No asset or derived CAD/mesh is packaged into Git or the Docker image.
