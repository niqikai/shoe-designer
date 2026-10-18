import trimesh


def hollow(outer, wall, name="shoe"):
    """Closed shell with an enclosed void: outward outer box + inward-facing inner box."""
    o = trimesh.creation.box(extents=outer)
    i = trimesh.creation.box(extents=[e - 2 * wall for e in outer])
    i.invert()
    m = trimesh.util.concatenate([o, i])
    m.apply_translation([outer[0] / 2, 0, outer[2] / 2])  # x from 0, z from 0
    return m
