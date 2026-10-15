"""
Wiggly Hole Generator - Geometry Engine for Compliant Flexural Fins, Coathanger Flexures & Crush Ribs
Handles cylindrical hole analysis, temporary B-Rep generation, and timeline integration.
"""

import math
import traceback
import adsk.core
import adsk.fusion


class FinDirection:
    CLOCKWISE = 'Clockwise'
    COUNTER_CLOCKWISE = 'Counter-Clockwise'


class CylinderData:
    """Extracts, validates, and aligns coordinate frames for cylindrical faces."""

    def __init__(self, face: adsk.fusion.BRepFace, flip_direction: bool = False):
        if not face or not face.isValid:
            raise ValueError("Invalid face selected.")

        geom = face.geometry
        if not isinstance(geom, adsk.core.Cylinder):
            raise ValueError("Selected face is not a cylinder.")

        self.face = face
        self.radius = geom.radius      # in cm
        self.diameter = self.radius * 2.0
        self.axis = geom.axis.copy()
        self.axis.normalize()
        self.origin = geom.origin.copy()

        # Compute axial extent along the cylinder axis
        t_values = []
        for edge in face.edges:
            if edge.startVertex:
                pt = edge.startVertex.geometry
                t = (pt.x - self.origin.x) * self.axis.x + \
                    (pt.y - self.origin.y) * self.axis.y + \
                    (pt.z - self.origin.z) * self.axis.z
                t_values.append(t)
            if edge.endVertex:
                pt = edge.endVertex.geometry
                t = (pt.x - self.origin.x) * self.axis.x + \
                    (pt.y - self.origin.y) * self.axis.y + \
                    (pt.z - self.origin.z) * self.axis.z
                t_values.append(t)
            try:
                ev = edge.evaluator
                ret, s_pt, e_pt = ev.getEndPoints()
                if ret and s_pt:
                    t = (s_pt.x - self.origin.x) * self.axis.x + \
                        (s_pt.y - self.origin.y) * self.axis.y + \
                        (s_pt.z - self.origin.z) * self.axis.z
                    t_values.append(t)
                if ret and e_pt:
                    t = (e_pt.x - self.origin.x) * self.axis.x + \
                        (e_pt.y - self.origin.y) * self.axis.y + \
                        (e_pt.z - self.origin.z) * self.axis.z
                    t_values.append(t)
            except:
                pass

        if not t_values:
            bb = face.boundingBox
            p1 = bb.minPoint
            p2 = bb.maxPoint
            t_values = [
                (p1.x - self.origin.x) * self.axis.x + (p1.y - self.origin.y) * self.axis.y + (p1.z - self.origin.z) * self.axis.z,
                (p2.x - self.origin.x) * self.axis.x + (p2.y - self.origin.y) * self.axis.y + (p2.z - self.origin.z) * self.axis.z
            ]

        self.t_min = min(t_values)
        self.t_max = max(t_values)
        self.length = max(0.001, self.t_max - self.t_min)

        # Check internal hole vs external cylinder (boss)
        self.is_internal = True
        try:
            pt_face = face.pointOnFace
            ret, normal = face.evaluator.getNormalAtPoint(pt_face)
            if ret and normal:
                t_pt = (pt_face.x - self.origin.x) * self.axis.x + \
                       (pt_face.y - self.origin.y) * self.axis.y + \
                       (pt_face.z - self.origin.z) * self.axis.z
                pt_axis = adsk.core.Point3D.create(
                    self.origin.x + t_pt * self.axis.x,
                    self.origin.y + t_pt * self.axis.y,
                    self.origin.z + t_pt * self.axis.z
                )
                vec_to_axis = pt_face.vectorTo(pt_axis)
                if normal.dotProduct(vec_to_axis) < 0:
                    self.is_internal = False
        except:
            pass

        # Setup local coordinate frame
        self.flip_direction = flip_direction
        if flip_direction:
            self.p_entrance = adsk.core.Point3D.create(
                self.origin.x + self.t_max * self.axis.x,
                self.origin.y + self.t_max * self.axis.y,
                self.origin.z + self.t_max * self.axis.z
            )
            self.z_dir = adsk.core.Vector3D.create(-self.axis.x, -self.axis.y, -self.axis.z)
        else:
            self.p_entrance = adsk.core.Point3D.create(
                self.origin.x + self.t_min * self.axis.x,
                self.origin.y + self.t_min * self.axis.y,
                self.origin.z + self.t_min * self.axis.z
            )
            self.z_dir = self.axis.copy()

        # Find orthogonal vectors X and Y in the cross-section
        ref = adsk.core.Vector3D.create(1.0, 0.0, 0.0)
        if abs(ref.dotProduct(self.z_dir)) > 0.85:
            ref = adsk.core.Vector3D.create(0.0, 1.0, 0.0)
        self.x_dir = self.z_dir.crossProduct(ref)
        self.x_dir.normalize()
        self.y_dir = self.z_dir.crossProduct(self.x_dir)
        self.y_dir.normalize()

        # Local to Global Transformation Matrix
        self.matrix_local_to_global = adsk.core.Matrix3D.create()
        self.matrix_local_to_global.setWithCoordinateSystem(
            self.p_entrance, self.x_dir, self.y_dir, self.z_dir
        )


def _make_box(tbm, center, length_dir, width_dir, length, width, height):
    """Creates a temporary BRep box using OrientedBoundingBox3D."""
    obb = adsk.core.OrientedBoundingBox3D.create(center, length_dir, width_dir, length, width, height)
    return tbm.createBox(obb)


def _make_tapered_fin_box(tbm, center_tip, u_fin, w_fin, l_fin, t_tip, t_root, height):
    """
    Creates a tapered prism starting at center_tip, extending by l_fin along u_fin,
    with thickness t_tip at tip and t_root at root, height along z.
    """
    if abs(t_root - t_tip) < 0.001:
        c = adsk.core.Point3D.create(
            center_tip.x + (l_fin / 2.0) * u_fin.x,
            center_tip.y + (l_fin / 2.0) * u_fin.y,
            center_tip.z
        )
        obb = adsk.core.OrientedBoundingBox3D.create(c, u_fin, w_fin, l_fin, t_tip, height)
        return tbm.createBox(obb)

    w_tip = t_tip / 2.0
    w_root = t_root / 2.0
    psi = math.atan2(w_root - w_tip, l_fin)

    l_side = (l_fin / math.cos(psi)) * 2.5
    w_box = max(t_root * 8.0, 2.0)

    cos_p = math.cos(psi)
    sin_p = math.sin(psi)

    u1 = adsk.core.Vector3D.create(cos_p * u_fin.x + sin_p * w_fin.x, cos_p * u_fin.y + sin_p * w_fin.y, 0.0)
    w1 = adsk.core.Vector3D.create(-sin_p * u_fin.x + cos_p * w_fin.x, -sin_p * u_fin.y + cos_p * w_fin.y, 0.0)

    p_mid1 = adsk.core.Point3D.create(
        center_tip.x + (l_fin / 2.0) * u_fin.x + ((w_tip + w_root) / 2.0) * w_fin.x,
        center_tip.y + (l_fin / 2.0) * u_fin.y + ((w_tip + w_root) / 2.0) * w_fin.y,
        center_tip.z
    )
    c1 = adsk.core.Point3D.create(
        p_mid1.x - (w_box / 2.0) * w1.x,
        p_mid1.y - (w_box / 2.0) * w1.y,
        p_mid1.z
    )
    box1 = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(c1, u1, w1, l_side, w_box, height))

    u2 = adsk.core.Vector3D.create(cos_p * u_fin.x - sin_p * w_fin.x, cos_p * u_fin.y - sin_p * w_fin.y, 0.0)
    w2 = adsk.core.Vector3D.create(sin_p * u_fin.x + cos_p * w_fin.x, sin_p * u_fin.y + cos_p * w_fin.y, 0.0)

    p_mid2 = adsk.core.Point3D.create(
        center_tip.x + (l_fin / 2.0) * u_fin.x - ((w_tip + w_root) / 2.0) * w_fin.x,
        center_tip.y + (l_fin / 2.0) * u_fin.y - ((w_tip + w_root) / 2.0) * w_fin.y,
        center_tip.z
    )
    c2 = adsk.core.Point3D.create(
        p_mid2.x + (w_box / 2.0) * w2.x,
        p_mid2.y + (w_box / 2.0) * w2.y,
        p_mid2.z
    )
    box2 = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(c2, u2, w2, l_side, w_box, height))

    tbm.booleanOperation(box1, box2, adsk.fusion.BooleanTypes.IntersectionBooleanType)

    # Clip off material before tip
    l_clip = l_fin * 3.0
    c_clip = adsk.core.Point3D.create(
        center_tip.x + (l_clip / 2.0) * u_fin.x,
        center_tip.y + (l_clip / 2.0) * u_fin.y,
        center_tip.z
    )
    box_clip = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(c_clip, u_fin, w_fin, l_clip, w_box * 2.0, height))
    tbm.booleanOperation(box1, box_clip, adsk.fusion.BooleanTypes.IntersectionBooleanType)
    return box1


def _trim_to_cylinder(tbm, body, r_outer, z_min, z_max):
    """Trims a body to lie within cylinder of radius r_outer between z_min and z_max."""
    p1 = adsk.core.Point3D.create(0.0, 0.0, z_min - 0.01)
    p2 = adsk.core.Point3D.create(0.0, 0.0, z_max + 0.01)
    cyl = tbm.createCylinderOrCone(p1, r_outer, p2, r_outer)
    tbm.booleanOperation(body, cyl, adsk.fusion.BooleanTypes.IntersectionBooleanType)


def _cut_lead_in(tbm, body, r_wall, r_inner, z_start, lead_in_len):
    """Subtracts an axisymmetric conical cutter at the entrance to form a smooth funnel lead-in chamfer."""
    if lead_in_len <= 0.0001:
        return body

    p1 = adsk.core.Point3D.create(0.0, 0.0, z_start)
    p2 = adsk.core.Point3D.create(0.0, 0.0, z_start + lead_in_len)
    cone = tbm.createCylinderOrCone(p1, r_wall + 0.02, p2, r_inner)

    # Also clear any material strictly before z_start
    p_pre1 = adsk.core.Point3D.create(0.0, 0.0, z_start - 0.5)
    p_pre2 = adsk.core.Point3D.create(0.0, 0.0, z_start)
    pre_cyl = tbm.createCylinderOrCone(p_pre1, r_wall + 0.02, p_pre2, r_wall + 0.02)
    tbm.booleanOperation(cone, pre_cyl, adsk.fusion.BooleanTypes.UnionBooleanType)

    tbm.booleanOperation(body, cone, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return body


def _pattern_ribs(tbm, single_rib, count):
    """Replicates single_rib around the Z-axis by count times and unions them."""
    if count <= 1:
        return single_rib

    d_theta = (2.0 * math.pi) / count
    accum = single_rib

    for i in range(1, count):
        rib_copy = tbm.copy(single_rib)
        mat = adsk.core.Matrix3D.create()
        mat.setToRotation(i * d_theta, adsk.core.Vector3D.create(0.0, 0.0, 1.0), adsk.core.Point3D.create(0, 0, 0))
        tbm.transform(rib_copy, mat)
        tbm.booleanOperation(accum, rib_copy, adsk.fusion.BooleanTypes.UnionBooleanType)

    return accum


def create_crush_ribs_body(cyl_data: CylinderData, params: dict):
    """
    Creates a temporary BRepBody containing crush ribs.
    All dimensions are expected in centimeters (Fusion internal units).
    """
    tbm = adsk.fusion.TemporaryBRepManager.get()
    radius = cyl_data.radius
    total_len = cyl_data.length

    count = int(params.get('count', 4))
    h = float(params.get('height', 0.03))
    w = float(params.get('width', 0.08))
    full_depth = bool(params.get('full_depth', True))
    depth_len = float(params.get('length', total_len))
    has_lead_in = bool(params.get('has_lead_in', True))
    lead_in_len = float(params.get('lead_in_length', 0.15))

    z0 = 0.0
    z1 = total_len if full_depth else min(total_len, depth_len)
    rib_len = z1 - z0
    if rib_len <= 0.001:
        return None

    embed = min(0.05, radius * 0.15)
    r_outer = radius + embed
    r_inner = max(0.005, radius - h)
    z_mid = (z0 + z1) / 2.0

    center = adsk.core.Point3D.create(radius - (h / 2.0), 0.0, z_mid)
    u_rad = adsk.core.Vector3D.create(1.0, 0.0, 0.0)
    w_tan = adsk.core.Vector3D.create(0.0, 1.0, 0.0)
    single_rib = _make_box(tbm, center, u_rad, w_tan, h + embed, w, rib_len)
    _trim_to_cylinder(tbm, single_rib, r_outer, z0, z1)

    lead_in = min(lead_in_len, rib_len * 0.75) if has_lead_in else 0.0
    if lead_in > 0.001:
        _cut_lead_in(tbm, single_rib, r_outer, r_inner, z0, lead_in)

    all_ribs = _pattern_ribs(tbm, single_rib, count)
    tbm.transform(all_ribs, cyl_data.matrix_local_to_global)
    return all_ribs


def create_compliant_fins_body(cyl_data: CylinderData, params: dict):
    """
    Creates a temporary BRepBody containing all compliant flexural fins.
    All dimensions are expected in centimeters (Fusion internal units).
    """
    tbm = adsk.fusion.TemporaryBRepManager.get()
    radius = cyl_data.radius
    total_len = cyl_data.length

    # Parse parameters
    count = int(params.get('count', 4))
    t_tip = float(params.get('thickness', 0.08))            # tip thickness (cm)
    t_root = float(params.get('thickness_root', t_tip * 1.5)) # root thickness (cm)
    slant_angle_deg = float(params.get('slant_angle_deg', 45.0))
    protrusion = float(params.get('protrusion', 0.1))       # cm
    direction = params.get('direction', FinDirection.CLOCKWISE)
    full_depth = bool(params.get('full_depth', True))
    depth_len = float(params.get('length', total_len))
    depth_offset = float(params.get('offset', 0.0))
    has_lead_in = bool(params.get('has_lead_in', True))
    lead_in_len = float(params.get('lead_in_length', 0.15))

    # Clamp bounds to safe geometry
    t_tip = max(0.03, min(radius * 0.4, t_tip))
    t_root = max(0.03, min(radius * 0.8, t_root))
    protrusion = max(0.02, min(radius * 0.8, protrusion))
    slant_deg = max(15.0, min(75.0, slant_angle_deg))
    if direction == FinDirection.COUNTER_CLOCKWISE:
        slant_deg = -slant_deg
    slant_angle = math.radians(slant_deg)

    if full_depth:
        z0 = 0.0
        z1 = total_len
    else:
        z0 = max(0.0, depth_offset)
        z1 = min(total_len, z0 + depth_len)

    rib_len = z1 - z0
    if rib_len <= 0.001:
        return None

    lead_in = min(lead_in_len, rib_len * 0.75) if has_lead_in else 0.0
    embed = min(0.05, radius * 0.15)
    r_outer = radius + embed
    r_inner = max(0.005, radius - protrusion)
    z_mid = (z0 + z1) / 2.0

    # Calculate fin length from inner tip to outer wall
    cos_s = math.cos(slant_angle)
    sin_s = math.sin(slant_angle)
    b = 2.0 * r_inner * cos_s
    c = (r_inner ** 2) - (r_outer ** 2)
    s_fin = (-b + math.sqrt(max(0.0001, b ** 2 - 4.0 * c))) / 2.0
    l_fin = s_fin + 0.05

    # Center tip of fin in local coordinate frame
    u_fin = adsk.core.Vector3D.create(cos_s, sin_s, 0.0)
    w_fin = adsk.core.Vector3D.create(-sin_s, cos_s, 0.0)
    center_tip = adsk.core.Point3D.create(r_inner, 0.0, z_mid)

    # Generate tapered or uniform fin prism
    single_fin = _make_tapered_fin_box(tbm, center_tip, u_fin, w_fin, l_fin, t_tip, t_root, rib_len)
    _trim_to_cylinder(tbm, single_fin, r_outer, z0, z1)

    # Conical lead-in chamfer for fin entrance
    if lead_in > 0.001:
        _cut_lead_in(tbm, single_fin, r_outer, r_inner, z0, lead_in)

    # Replicate around Z axis and transform to world coordinate frame
    all_fins = _pattern_ribs(tbm, single_fin, count)
    tbm.transform(all_fins, cyl_data.matrix_local_to_global)
    return all_fins


def create_coathanger_cutter(cyl_data: CylinderData, params: dict):
    """
    Creates the cutting tool body to subtract the relief slot and parting slit for Coathanger Flexures.
    Features:
    - Bounded radial slit that strictly terminates at the relief slot outer wall (never cuts past).
    - Fully filleted / semicircular end caps on the relief slot arc.
    - Fully rounded semicircular tips on the compliant flexure fingers at the slit.
    """
    tbm = adsk.fusion.TemporaryBRepManager.get()
    radius = cyl_data.radius
    total_len = cyl_data.length

    wall_thick = float(params.get('wall_thickness', 0.12))  # cm
    slot_width = float(params.get('slot_width', 0.15))      # cm
    arc_deg = float(params.get('arc_deg', 120.0))          # degrees
    slit_width = float(params.get('slit_width', 0.10))      # cm
    rot_deg = float(params.get('rotation_deg', 0.0))        # degrees

    r_inner = radius + wall_thick
    r_outer = r_inner + slot_width
    r_mid = (r_inner + r_outer) / 2.0
    r_slot_cap = slot_width / 2.0

    z_min = -0.05
    z_max = total_len + 0.05
    h_cut = z_max - z_min
    z_mid = (z_min + z_max) / 2.0
    p1 = adsk.core.Point3D.create(0, 0, z_min)
    p2 = adsk.core.Point3D.create(0, 0, z_max)

    # 1. Outer slot tube
    cyl_outer = tbm.createCylinderOrCone(p1, r_outer, p2, r_outer)
    cyl_inner = tbm.createCylinderOrCone(p1, r_inner, p2, r_inner)
    tbm.booleanOperation(cyl_outer, cyl_inner, adsk.fusion.BooleanTypes.DifferenceBooleanType)

    # 2. Intersect with exact radial bounding planes for arc span
    half_angle = math.radians(min(179.0, arc_deg) / 2.0)
    box_size = r_outer * 4.0

    n1 = adsk.core.Vector3D.create(math.sin(half_angle), -math.cos(half_angle), 0.0)
    u1 = adsk.core.Vector3D.create(math.cos(half_angle), math.sin(half_angle), 0.0)
    c1 = adsk.core.Point3D.create((box_size / 2.0) * n1.x, (box_size / 2.0) * n1.y, z_mid)
    box1 = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(c1, u1, n1, box_size, box_size, h_cut))
    tbm.booleanOperation(cyl_outer, box1, adsk.fusion.BooleanTypes.IntersectionBooleanType)

    n2 = adsk.core.Vector3D.create(math.sin(half_angle), math.cos(half_angle), 0.0)
    u2 = adsk.core.Vector3D.create(math.cos(half_angle), -math.sin(half_angle), 0.0)
    c2 = adsk.core.Point3D.create((box_size / 2.0) * n2.x, (box_size / 2.0) * n2.y, z_mid)
    box2 = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(c2, u2, n2, box_size, box_size, h_cut))
    tbm.booleanOperation(cyl_outer, box2, adsk.fusion.BooleanTypes.IntersectionBooleanType)

    # 3. Add semicircular end caps to the slot
    p_cap1_start = adsk.core.Point3D.create(r_mid * math.cos(half_angle), r_mid * math.sin(half_angle), z_min)
    p_cap1_end = adsk.core.Point3D.create(r_mid * math.cos(half_angle), r_mid * math.sin(half_angle), z_max)
    cap1 = tbm.createCylinderOrCone(p_cap1_start, r_slot_cap, p_cap1_end, r_slot_cap)
    tbm.booleanOperation(cyl_outer, cap1, adsk.fusion.BooleanTypes.UnionBooleanType)

    p_cap2_start = adsk.core.Point3D.create(r_mid * math.cos(-half_angle), r_mid * math.sin(-half_angle), z_min)
    p_cap2_end = adsk.core.Point3D.create(r_mid * math.cos(-half_angle), r_mid * math.sin(-half_angle), z_max)
    cap2 = tbm.createCylinderOrCone(p_cap2_start, r_slot_cap, p_cap2_end, r_slot_cap)
    tbm.booleanOperation(cyl_outer, cap2, adsk.fusion.BooleanTypes.UnionBooleanType)

    # 4. Slit cutter with rounded finger tips
    r_finger_cap = wall_thick / 2.0
    r_f_mid = radius + wall_thick / 2.0
    y_cap = (slit_width / 2.0) + r_finger_cap

    slit_start_x = radius - 0.02
    slit_end_x = r_outer
    slit_len = slit_end_x - slit_start_x
    c_slit = adsk.core.Point3D.create(slit_start_x + slit_len / 2.0, 0, z_mid)
    slit_box = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(
        c_slit,
        adsk.core.Vector3D.create(1, 0, 0),
        adsk.core.Vector3D.create(0, 1, 0),
        slit_len,
        2.0 * y_cap,
        h_cut
    ))

    # Subtract finger round caps so the remaining fingers have smooth rounded tips
    p_fc1_start = adsk.core.Point3D.create(r_f_mid, y_cap, z_min)
    p_fc1_end = adsk.core.Point3D.create(r_f_mid, y_cap, z_max)
    finger_cap1 = tbm.createCylinderOrCone(p_fc1_start, r_finger_cap, p_fc1_end, r_finger_cap)
    tbm.booleanOperation(slit_box, finger_cap1, adsk.fusion.BooleanTypes.DifferenceBooleanType)

    p_fc2_start = adsk.core.Point3D.create(r_f_mid, -y_cap, z_min)
    p_fc2_end = adsk.core.Point3D.create(r_f_mid, -y_cap, z_max)
    finger_cap2 = tbm.createCylinderOrCone(p_fc2_start, r_finger_cap, p_fc2_end, r_finger_cap)
    tbm.booleanOperation(slit_box, finger_cap2, adsk.fusion.BooleanTypes.DifferenceBooleanType)

    # Union slit with outer slot
    tbm.booleanOperation(cyl_outer, slit_box, adsk.fusion.BooleanTypes.UnionBooleanType)

    # Strictly clip to cylinder of radius r_outer so cutter never cuts beyond the slot
    cyl_boundary = tbm.createCylinderOrCone(p1, r_outer, p2, r_outer)
    tbm.booleanOperation(cyl_outer, cyl_boundary, adsk.fusion.BooleanTypes.IntersectionBooleanType)

    # 5. Rotate around hole axis by rotation angle
    if abs(rot_deg) > 0.01:
        mat_rot = adsk.core.Matrix3D.create()
        mat_rot.setToRotation(math.radians(rot_deg), adsk.core.Vector3D.create(0, 0, 1), adsk.core.Point3D.create(0, 0, 0))
        tbm.transform(cyl_outer, mat_rot)

    # 6. Transform to world coordinate frame
    tbm.transform(cyl_outer, cyl_data.matrix_local_to_global)
    return cyl_outer


def apply_cutter_body(parent_component: adsk.fusion.Component, target_body: adsk.fusion.BRepBody, cutter_body: adsk.fusion.BRepBody, feature_name: str = "Coathanger Flexure", create_group: bool = False):
    """
    Subtracts the cutter body from the target body via Combine Cut.
    Returns a dict with internal timeline features.
    """
    design = parent_component.parentDesign
    is_parametric = (design.designType == adsk.fusion.DesignTypes.ParametricDesignType)

    if is_parametric:
        base_feature = parent_component.features.baseFeatures.add()
        base_feature.startEdit()
        parent_component.bRepBodies.add(cutter_body, base_feature)
        base_feature.name = f"{feature_name} Cutter"
        base_feature.finishEdit()

        tool_bodies = adsk.core.ObjectCollection.create()
        for b in base_feature.bodies:
            tool_bodies.add(b)

        combine_input = parent_component.features.combineFeatures.createInput(target_body, tool_bodies)
        combine_input.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
        combine_input.isNewComponent = False
        combine_input.isKeepToolBodies = False
        combine_feature = parent_component.features.combineFeatures.add(combine_input)
        combine_feature.name = feature_name

        if create_group:
            try:
                design.timeline.timelineGroups.add(base_feature.timelineObject.index, combine_feature.timelineObject.index)
            except:
                pass

        return {
            'base_feature': base_feature,
            'combine_feature': combine_feature,
            'fillet_feature': None,
            'end_feature': combine_feature,
            'result_body': target_body
        }
    else:
        added_body = parent_component.bRepBodies.add(cutter_body)
        tool_bodies = adsk.core.ObjectCollection.create()
        tool_bodies.add(added_body)
        combine_input = parent_component.features.combineFeatures.createInput(target_body, tool_bodies)
        combine_input.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
        combine_input.isNewComponent = False
        combine_input.isKeepToolBodies = False
        combine_feature = parent_component.features.combineFeatures.add(combine_input)
        return {
            'base_feature': None,
            'combine_feature': combine_feature,
            'fillet_feature': None,
            'end_feature': combine_feature,
            'result_body': target_body
        }


def _find_root_edges(target_body: adsk.fusion.BRepBody, cyl_data: CylinderData) -> adsk.core.ObjectCollection:
    """Finds the concave intersection edges on target_body where the fin blades meet the cylindrical hole wall."""
    root_edges = adsk.core.ObjectCollection.create()
    if not target_body or not target_body.isValid or not cyl_data:
        return root_edges

    radius = cyl_data.radius
    origin = cyl_data.origin
    axis = cyl_data.axis

    try:
        concave_set = {e.tempId for e in target_body.concaveEdges}
    except:
        concave_set = None

    for edge in target_body.edges:
        if not edge.isValid or edge.length < 0.005:
            continue

        # Check concavity if available
        if concave_set is not None and edge.tempId not in concave_set:
            continue

        # Check orientation: must be parallel to cylinder axis
        try:
            ev = edge.evaluator
            ret, s_pt, e_pt = ev.getEndPoints()
            if not ret or not s_pt or not e_pt:
                continue
            dx = e_pt.x - s_pt.x
            dy = e_pt.y - s_pt.y
            dz = e_pt.z - s_pt.z
            seg_len = math.hypot(dx, dy, dz)
            if seg_len < 0.005:
                continue
            dot = (dx * axis.x + dy * axis.y + dz * axis.z) / seg_len
            if abs(dot) < 0.85:
                continue
        except:
            continue

        # Check distance from cylinder axis at midpoint
        try:
            pt = edge.pointOnEdge
            vx = pt.x - origin.x
            vy = pt.y - origin.y
            vz = pt.z - origin.z
            proj = vx * axis.x + vy * axis.y + vz * axis.z
            rx = vx - proj * axis.x
            ry = vy - proj * axis.y
            rz = vz - proj * axis.z
            r_dist = math.hypot(rx, ry, rz)
            if abs(r_dist - radius) > min(0.05, radius * 0.1):
                continue
        except:
            continue

        root_edges.add(edge)

    return root_edges


def _apply_root_fillets(parent_component: adsk.fusion.Component, target_body: adsk.fusion.BRepBody, cyl_data: CylinderData, fillet_radius: float, feature_name: str):
    """Creates a fillet feature on the root edges where fins join the hole wall."""
    root_edges = _find_root_edges(target_body, cyl_data)
    if root_edges.count == 0:
        return None

    fillet_feats = parent_component.features.filletFeatures

    # Try requested radius, then gracefully scale down if needed to prevent geometry failure
    for r_try in [fillet_radius, min(fillet_radius, 0.04), min(fillet_radius, 0.02)]:
        if r_try <= 0.001:
            break
        try:
            fillet_input = fillet_feats.createInput()
            radius_val = adsk.core.ValueInput.createByReal(r_try)
            fillet_input.edgeSetInputs.addConstantRadiusEdgeSet(root_edges, radius_val, False)
            fillet_feat = fillet_feats.add(fillet_input)
            fillet_feat.name = f"{feature_name} Fillet"
            return fillet_feat
        except:
            continue

    return None


def apply_tool_body(parent_component: adsk.fusion.Component, target_body: adsk.fusion.BRepBody, tool_body: adsk.fusion.BRepBody, feature_name: str = "Compliant Fins", cyl_data: CylinderData = None, params: dict = None, create_group: bool = False):
    """
    Applies the tool body to the target body via Combine Join,
    optionally fillets the root edges, and returns internal timeline features.
    """
    design = parent_component.parentDesign
    is_parametric = (design.designType == adsk.fusion.DesignTypes.ParametricDesignType)

    has_fillet = bool(params.get('has_fillet', False)) if params else False
    fillet_radius = float(params.get('fillet_radius', 0.0)) if params else 0.0

    if is_parametric:
        base_feature = parent_component.features.baseFeatures.add()
        base_feature.startEdit()
        parent_component.bRepBodies.add(tool_body, base_feature)
        base_feature.name = f"{feature_name} Solid"
        base_feature.finishEdit()

        tool_bodies = adsk.core.ObjectCollection.create()
        for b in base_feature.bodies:
            tool_bodies.add(b)

        combine_input = parent_component.features.combineFeatures.createInput(target_body, tool_bodies)
        combine_input.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
        combine_input.isNewComponent = False
        combine_input.isKeepToolBodies = False
        combine_feature = parent_component.features.combineFeatures.add(combine_input)
        combine_feature.name = feature_name

        result_body = combine_feature.bodies.item(0) if (combine_feature and combine_feature.bodies.count > 0) else target_body

        fillet_feature = None
        if has_fillet and fillet_radius > 0.001 and cyl_data:
            fillet_feature = _apply_root_fillets(parent_component, result_body, cyl_data, fillet_radius, feature_name)

        if create_group:
            timeline_end = fillet_feature.timelineObject.index if fillet_feature else combine_feature.timelineObject.index
            try:
                design.timeline.timelineGroups.add(base_feature.timelineObject.index, timeline_end)
            except:
                pass

        return {
            'base_feature': base_feature,
            'combine_feature': combine_feature,
            'fillet_feature': fillet_feature,
            'end_feature': fillet_feature if fillet_feature else combine_feature,
            'result_body': result_body
        }
    else:
        added_body = parent_component.bRepBodies.add(tool_body)
        tool_bodies = adsk.core.ObjectCollection.create()
        tool_bodies.add(added_body)
        combine_input = parent_component.features.combineFeatures.createInput(target_body, tool_bodies)
        combine_input.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
        combine_input.isNewComponent = False
        combine_input.isKeepToolBodies = False
        combine_feature = parent_component.features.combineFeatures.add(combine_input)

        result_body = combine_feature.bodies.item(0) if (combine_feature and combine_feature.bodies.count > 0) else target_body

        fillet_feature = None
        if has_fillet and fillet_radius > 0.001 and cyl_data:
            fillet_feature = _apply_root_fillets(parent_component, result_body, cyl_data, fillet_radius, feature_name)

        return {
            'base_feature': None,
            'combine_feature': combine_feature,
            'fillet_feature': fillet_feature,
            'end_feature': combine_feature,
            'result_body': result_body
        }


def update_base_feature_body(base_feature: adsk.fusion.BaseFeature, new_tool_body: adsk.fusion.BRepBody) -> bool:
    """Updates the B-Rep body inside an existing BaseFeature in-place."""
    if not base_feature or not base_feature.isValid or not new_tool_body or not new_tool_body.isValid:
        return False
    try:
        base_feature.startEdit()
        if base_feature.bodies.count > 0:
            existing_body = base_feature.bodies.item(0)
            base_feature.updateBody(existing_body, new_tool_body)
        else:
            base_feature.parentComponent.bRepBodies.add(new_tool_body, base_feature)
        base_feature.finishEdit()
        return True
    except:
        return False


def update_custom_feature_geometry(custom_feat: adsk.fusion.CustomFeature, target_body: adsk.fusion.BRepBody, cyl_data: CylinderData, params: dict) -> bool:
    """
    Regenerates geometry for an existing CustomFeature in-place.
    Updates the BaseFeature solid/cutter body using native updateBody.
    """
    if not custom_feat or not custom_feat.isValid or not target_body or not cyl_data:
        return False

    mode = params.get('type', 'Compliant Fins')

    # 1. Generate new tool/cutter body
    if mode == 'Compliant Fins':
        new_body = create_compliant_fins_body(cyl_data, params)
    elif mode == 'Coathanger':
        new_body = create_coathanger_cutter(cyl_data, params)
    elif mode == 'Crush Ribs':
        new_body = create_crush_ribs_body(cyl_data, params)
    else:
        return False

    if not new_body or not new_body.isValid:
        return False

    # 2. Locate BaseFeature child inside CustomFeature
    base_feat = None
    for feat in custom_feat.features:
        if isinstance(feat, adsk.fusion.BaseFeature):
            base_feat = feat
            break

    if not base_feat or not base_feat.isValid:
        return False

    # 3. Update the BaseFeature body in place
    ok = update_base_feature_body(base_feat, new_body)
    if not ok:
        return False

    # 4. If fillet feature exists, update its radius parameter
    if params.get('has_fillet', False):
        fillet_radius = float(params.get('fillet_radius', 0.05))
        for feat in custom_feat.features:
            if isinstance(feat, adsk.fusion.FilletFeature):
                try:
                    for es in feat.edgeSets:
                        if hasattr(es, 'radius'):
                            es.radius.value = fillet_radius
                except:
                    pass
                break

    return True
