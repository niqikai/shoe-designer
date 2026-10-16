"""Explicit curved underfoot design envelope, independent of raster resolution.

The accepted shoe/cavity CAD remain the exact material boundaries. This roof
selects a conservative design region below the accepted lower-foot connection
guide. It is a piecewise-linear contract, not a reconstructed source sole.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class SoleEnvelope:
    metadata: dict

    def __post_init__(self):
        points = np.asarray(self.metadata['upper_points_xz_mm'], float)
        if (self.metadata.get('version') != 'lower-foot-roof-polyline-v1'
                or points.ndim != 2 or points.shape[1] != 2 or len(points) < 2
                or not np.isfinite(points).all() or np.any(np.diff(points[:, 0]) <= 0)
                or not np.isfinite(self.metadata['lower_datum_z_mm'])
                or np.any(points[:, 1] <= self.metadata['lower_datum_z_mm'])):
            raise ValueError('Invalid curved sole envelope contract')

    @classmethod
    def from_metadata(cls, metadata):
        return None if metadata is None else cls(metadata)

    @property
    def points(self):
        return np.asarray(self.metadata['upper_points_xz_mm'], float)

    @property
    def z_bounds(self):
        return float(self.metadata['lower_datum_z_mm']), float(self.points[:, 1].max())

    def upper_z(self, x):
        p = self.points
        return np.interp(x, p[:, 0], p[:, 1])

    def contains(self, points):
        points = np.asarray(points, float)
        return (points[..., 2] > self.z_bounds[0]) & (points[..., 2] < self.upper_z(points[..., 0]))

    def boundary_distance(self, point):
        """Exact Euclidean distance to this ruled roof and the lower datum.

        The roof is constant in Y, so 2D segment distance equals its 3D
        surface distance. Endpoint horizontal rays implement constant extension.
        """
        p = self.points
        q = np.asarray(point, float)[[0, 2]]
        a, d = p[:-1], np.diff(p, axis=0)
        t = np.clip(np.sum((q-a)*d, axis=1)/np.sum(d*d, axis=1), 0, 1)
        distances = np.linalg.norm(q-(a+t[:, None]*d), axis=1)
        left = np.linalg.norm(q-[min(q[0], p[0, 0]), p[0, 1]])
        right = np.linalg.norm(q-[max(q[0], p[-1, 0]), p[-1, 1]])
        return float(min(distances.min(), left, right, q[1]-self.z_bounds[0]))

    def x_intervals(self, z):
        """Exact X intervals below the roof, restricted to recorded X span."""
        if not self.z_bounds[0] < z < self.z_bounds[1]:
            return []
        result = []
        for a, b in zip(self.points[:-1], self.points[1:]):
            if max(a[1], b[1]) <= z:
                continue
            low, high = a[0], b[0]
            if min(a[1], b[1]) <= z:
                crossing = a[0]+(z-a[1])*(b[0]-a[0])/(b[1]-a[1])
                if a[1] <= z:
                    low = crossing
                else:
                    high = crossing
            if result and abs(result[-1][1]-low) < 1e-9:
                result[-1][1] = float(high)
            else:
                result.append([float(low), float(high)])
        return result


def build_sole_envelope(shape, last, spec, geometry_report, *, source_sha256):
    """Derive a deterministic roof from hash-locked accepted M1 evidence."""
    if spec.lattice.domain.mode == 'planar':
        return None
    from shoe_poc.geometry.sole import _rocker
    sole = geometry_report['sole']
    profile = spec.sole.profile_parameters
    if sole['actual_profile_parameters'] != profile.model_dump():
        raise ValueError('Frozen sole guide parameters disagree with accepted spec')
    box, last_box = shape.bounding_box(), last.bounding_box()
    stations = np.asarray(sole['station_x_mm'], float)
    guide = np.asarray([s['connection_z_mm'] for s in sole['lower_foot_station_profiles']], float)
    if (stations.shape != guide.shape or not np.isfinite(guide).all()
            or np.any(np.diff(stations) <= 0)):
        raise ValueError('Invalid frozen lower-foot connection guide')
    step = 1.0
    xs = np.unique(np.r_[box.min.X, np.arange(box.min.X, box.max.X, step),
                         stations, box.max.X])
    xs = xs[(xs >= box.min.X) & (xs <= box.max.X)]
    band = float(sole['transition']['band_half_height_mm'])
    if not np.isfinite(band) or band <= 0:
        raise ValueError('Frozen transition band must be positive')
    datum = float(last_box.min.Z-spec.sole.height_mm)
    rocker = np.array([_rocker(float(x), last_box, profile) for x in xs])
    upper = np.minimum(last_box.min.Z+rocker,
                       np.interp(xs, stations, guide)-band)+spec.lattice.domain.max_z_offset_mm
    return SoleEnvelope({'version': 'lower-foot-roof-polyline-v1',
        'mode': 'curved_lower_foot', 'lower_datum_z_mm': datum,
        'upper_points_xz_mm': np.column_stack([xs, upper]).tolist(),
        'source_geometry_report_sha256': source_sha256,
        'maximum_station_spacing_mm': step,
        'transition_band_reserve_mm': band,
        'max_z_offset_mm': spec.lattice.domain.max_z_offset_mm,
        'roof_rule': 'min(accepted rocker + nominal sole height, frozen lower-foot connection guide - transition half band) + max_z_offset',
        'interpolation': 'Linear in X, constant in Y; end guide values clamped. These explicit segments define the contract; no smooth-curve approximation claim.',
        'lower_boundary': 'Accepted shoe CAD exterior including exact rocker and fillet, plus lower datum',
        'limitations': 'Design subset from accepted sampled connection guide, not the original sole B-Rep or a proof of global sole/upper semantic separation.'})
