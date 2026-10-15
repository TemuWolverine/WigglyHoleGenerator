"""
Configuration, constants, and default presets for Wiggly Hole Generator (Crush Ribs, Compliant Fins, Coathanger Flexures).
"""

import math

COMMAND_ID = 'WigglyHoleGeneratorCommand'
LEGACY_COMMAND_ID = 'CrushFinsCommand'
COMMAND_NAME = 'Wiggly Hole Generator'
COMMAND_DESCRIPTION = 'Generate Compliant Flexural Fins, Coathanger Flexures, or Crush Ribs inside cylindrical holes.'
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidCreatePanel'
CONTROL_BEFORE_ID = 'Thread'


class FeatureType:
    CRUSH_RIBS = 'Crush Ribs'
    COMPLIANT_FINS = 'Compliant Fins'


class RibProfile:
    TRIANGULAR = 'Triangular (Pointed)'
    ROUNDED = 'Rounded (Semicircular)'
    TRAPEZOIDAL = 'Trapezoidal (Flat)'


class FinDirection:
    CLOCKWISE = 'Clockwise'
    COUNTER_CLOCKWISE = 'Counter-Clockwise'


class CrushRibDefaults:
    COUNT = 4
    HEIGHT_RATIO = 0.05       # 5% of hole diameter
    MIN_HEIGHT_MM = 0.2
    MAX_HEIGHT_MM = 2.0
    APEX_ANGLE_DEG = 60.0
    FULL_DEPTH = True
    HAS_LEAD_IN = True
    LEAD_IN_LENGTH_MM = 1.5
    FLIP_DIRECTION = False


class CompliantFinDefaults:
    COUNT = 4
    THICKNESS_MM = 0.8
    SLANT_ANGLE_DEG = 45.0
    PROTRUSION_RATIO = 0.08    # 8% of hole diameter
    DIRECTION = FinDirection.CLOCKWISE
    FULL_DEPTH = True
    HAS_LEAD_IN = True
    LEAD_IN_LENGTH_MM = 1.5
    HAS_FILLET = True
    FILLET_RADIUS_MM = 0.5
    FLIP_DIRECTION = False


def calculate_crush_rib_defaults(diameter_mm: float, depth_mm: float):
    """Computes proportional defaults for crush ribs based on hole size."""
    h = max(CrushRibDefaults.MIN_HEIGHT_MM, min(CrushRibDefaults.MAX_HEIGHT_MM, diameter_mm * CrushRibDefaults.HEIGHT_RATIO))
    w = max(0.5, h * 2.0 * math.tan(math.radians(CrushRibDefaults.APEX_ANGLE_DEG / 2.0)))
    lead_in = min(max(1.0, depth_mm * 0.15), 3.0)
    count = 3 if diameter_mm < 5.0 else (6 if diameter_mm > 16.0 else 4)
    return {
        'count': count,
        'height_mm': round(h, 2),
        'width_mm': round(w, 2),
        'apex_angle_deg': CrushRibDefaults.APEX_ANGLE_DEG,
        'lead_in_length_mm': round(lead_in, 2)
    }


def calculate_compliant_fin_defaults(diameter_mm: float, depth_mm: float):
    """Computes proportional defaults for compliant fins based on hole size."""
    protrusion = max(0.3, min(2.5, diameter_mm * CompliantFinDefaults.PROTRUSION_RATIO))
    thickness = 0.8 if diameter_mm < 10.0 else 1.0
    lead_in = min(max(1.0, depth_mm * 0.15), 3.0)
    count = 3 if diameter_mm < 6.0 else 4
    fillet_r = round(min(0.8, max(0.3, thickness * 0.5)), 2)
    return {
        'count': count,
        'thickness_mm': round(thickness, 2),
        'slant_angle_deg': CompliantFinDefaults.SLANT_ANGLE_DEG,
        'protrusion_mm': round(protrusion, 2),
        'lead_in_length_mm': round(lead_in, 2),
        'has_fillet': CompliantFinDefaults.HAS_FILLET,
        'fillet_radius_mm': fillet_r
    }
