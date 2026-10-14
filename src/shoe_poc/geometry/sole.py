"""Actual lower-foot section widths and a closed CAD sole with a curved junction."""
from __future__ import annotations

import numpy as np

ALGORITHM_VERSION = "cad-lower-foot-periodic-rocker-sole-v7"


def _rocker(x, box, profile):
    t = np.clip((x-box.min.X)/box.size.X, 0, 1)
    toe_start = getattr(profile, 'toe_start_fraction', .68)
    def smooth(q):
        return q*q*q*(10+q*(-15+6*q))
    return (profile.heel_rise_mm*smooth(np.clip((.22-t)/.22, 0, 1))
            + profile.toe_rise_mm*smooth(np.clip((t-toe_start)/(1-toe_start), 0, 1)))


def _lower_foot_sections(last_shape, spec):
    from build123d import Solid, Plane
    from .envelope import _section_points
    box = last_shape.bounding_box()
    clip = Solid.make_box(box.size.X+20, box.size.Y+20, box.size.Z*.66+10,
                          Plane(origin=(box.min.X-10, box.min.Y-10, box.min.Z-10)))
    foot = last_shape.intersect(clip)
    if foot is None or not foot.is_valid:
        raise ValueError("Lower foot partition is not a valid CAD shape")
    xs = np.unique(np.r_[np.arange(box.min.X+3, box.max.X-3, 4), box.max.X-3])
    data = []
    for x in xs:
        section = _section_points(foot, float(x), 256)
        floor, top = float(section[:, 2].min()), float(section[:, 2].max())
        low = section[section[:, 2] <= floor+min(18., (top-floor)*.8)]
        data.append({"x_mm": float(x), "last_floor_z_mm": floor,
                     "left_y_mm": float(low[:, 1].min()-spec.sole.profile_parameters.margin_mm),
                     "right_y_mm": float(low[:, 1].max()+spec.sole.profile_parameters.margin_mm),
                     "connection_z_mm": floor+min(14., (top-floor)*.6)})
    return {"station_x_mm": xs.tolist(), "lower_foot_station_profiles": data,
            "section_count": len(xs), "sampling_points_per_section": 256,
            "footprint_method": "True CAD transverse contours within 18 mm of local floor, capped at 80% of small section height; periodic contour preserves medial/forefoot width changes"}


class SoleGenerator:
    def generate(self, last_shape, spec, outer=None):
        from .sole_transition import periodic_transition
        from .envelope import section_envelopes
        box = last_shape.bounding_box()
        if outer is None:
            _, outer, _ = section_envelopes(last_shape.solids()[0], spec.upper.clearance_mm,
                                          spec.upper.thickness_mm)
        metadata = _lower_foot_sections(last_shape, spec)
        bottom = box.min.Z-spec.sole.height_mm
        result, transition = periodic_transition(last_shape, outer, bottom, metadata, spec)
        measured = result.bounding_box()
        if abs(measured.min.Z-bottom) > 1e-5:
            raise ValueError(f"Sole datum failed: {measured.min.Z} vs {bottom} mm")
        if measured.size.Y > box.size.Y+2*spec.sole.profile_parameters.margin_mm+1:
            raise ValueError("Sole exceeds its lower-foot width bounds")
        profile = spec.sole.profile_parameters
        from shoe_poc.validation.junction import measure_bottom_round
        round_validation = measure_bottom_round(result, box, spec)
        if round_validation['geometry_status'] == 'FAIL':
            raise ValueError(f"Actual bottom round failed: {round_validation['maximum_radius_deviation_mm']} mm radius deviation")
        metadata.update({
            "algorithm_version": ("cad-lower-foot-periodic-rocker-sole-v8" if profile.sidewall_bulge_mm else ALGORITHM_VERSION),
            "method": "Closed periodic lower-foot CAD skin, actual exterior normal-constrained junction, exact quintic rocker floor and native bottom fillet",
            "transition": transition, "actual_profile_parameters": profile.model_dump(),
            "bottom_round_validation": round_validation,
            "margin_mm": profile.margin_mm, "sidewall_inset_mm": profile.sidewall_inset_mm,
            "toe_rise_mm": profile.toe_rise_mm, "heel_rise_mm": profile.heel_rise_mm,
            "edge_radius_mm": profile.edge_radius_mm, "height_parameter_mm": spec.sole.height_mm,
            "bottom_z_mm": bottom, "measured_bottom_z_mm": measured.min.Z,
            "last_min_z_to_lowest_sole_z_mm": box.min.Z-measured.min.Z,
            "bbox_mm": {"min": list(measured.min), "max": list(measured.max)},
            "stock_volume_mm3": result.volume, "stock_face_count": len(result.faces()),
            "surface_continuity": "Shared Q and Q +/- D/3 cubic control columns preserve the periodic band tangents; final exterior join requires independent CAD measurements",
            "limitations": ["Lower contour is sampled from genuine CAD sections; global contour approximation error is not certified.",
                            "Finite junction and curvature measurements cannot establish global smoothness or physical performance."],
        })
        return result, metadata
