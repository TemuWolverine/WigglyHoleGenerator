"""
Wiggly Hole Generator - Autodesk Fusion 360 Add-In
Automated generation of Compliant Flexural Fins, Coathanger Flexures & Crush Ribs inside cylindrical holes.
Powered by declarative JSON UI schema (ui_schema.json) and native CustomFeature timeline integration.
"""

import os
import json
import math
import traceback
import adsk.core
import adsk.fusion

try:
    from .geometry import (
        CylinderData, FinDirection, create_compliant_fins_body,
        create_coathanger_cutter, create_crush_ribs_body,
        apply_tool_body, apply_cutter_body, update_custom_feature_geometry
    )
    from .ui_builder import SchemaUIBuilder
except ImportError:
    from geometry import (
        CylinderData, FinDirection, create_compliant_fins_body,
        create_coathanger_cutter, create_crush_ribs_body,
        apply_tool_body, apply_cutter_body, update_custom_feature_geometry
    )
    from ui_builder import SchemaUIBuilder

_app = None
_ui = None
_handlers = []
_active_ui_builder = None

# Custom Feature & Command Identifiers
COMMAND_ID = 'WigglyHoleGeneratorCommand'
LEGACY_COMMAND_ID = 'CrushFinsCommand'
COMMAND_NAME = 'Wiggly Hole Generator'
COMMAND_DESCRIPTION = 'Generate Compliant Flexural Fins, Coathanger Flexures, or Crush Ribs inside cylindrical holes.'
PANEL_ID = 'SolidCreatePanel'

EDIT_COMMAND_ID = 'WigglyHoleGeneratorEditCommand'
EDIT_COMMAND_NAME = 'Edit Wiggly Hole'
CUSTOM_FEATURE_DEF_ID = 'adsk.fusion.custompackage3d'
CUSTOM_FEATURE_NAME = 'Wiggly Hole'

TYPE_COMPLIANT_FINS = 'Compliant Fins'
TYPE_COATHANGER = 'Coathanger'
TYPE_CRUSH_RIBS = 'Crush Ribs'

# Global State for Feature Lifecycle
_custom_feature_def = None
_edited_custom_feature = None
_restore_timeline_object = None
_is_rolled_for_edit = False


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, resource_dir):
        super().__init__()
        self.resource_dir = resource_dir

    def notify(self, args: adsk.core.CommandCreatedEventArgs):
        global _active_ui_builder
        try:
            cmd = args.command
            cmd.isRepeatable = True

            # Register lifecycle handlers
            on_input_changed = InputChangedHandler()
            cmd.inputChanged.add(on_input_changed)
            _handlers.append(on_input_changed)

            on_validate = ValidateInputsHandler()
            cmd.validateInputs.add(on_validate)
            _handlers.append(on_validate)

            on_preview = ExecutePreviewHandler()
            cmd.executePreview.add(on_preview)
            _handlers.append(on_preview)

            on_execute = ExecuteHandler()
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)

            on_destroy = DestroyHandler()
            cmd.destroy.add(on_destroy)
            _handlers.append(on_destroy)

            # Build dialog inputs dynamically from declarative schema
            current_dir = os.path.dirname(os.path.realpath(__file__))
            schema_path = os.path.join(current_dir, 'ui_schema.json')

            _active_ui_builder = SchemaUIBuilder(cmd.commandInputs, schema_path, self.resource_dir)

            # Pre-selection check: if a cylindrical face was already selected before opening
            if _ui and _ui.activeSelections.count > 0:
                pre_sel = _ui.activeSelections.item(0).entity
                if pre_sel and isinstance(pre_sel, adsk.fusion.BRepFace):
                    geom = pre_sel.geometry
                    if geom and geom.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType:
                        hole_sel = _active_ui_builder.get_item('holeSelection')
                        if hole_sel:
                            hole_sel.addSelection(pre_sel)
                            _update_hole_info(_active_ui_builder)

        except:
            if _ui:
                _ui.messageBox(f"Error in CommandCreated:\n{traceback.format_exc()}")


class EditCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, resource_dir):
        super().__init__()
        self.resource_dir = resource_dir

    def notify(self, args: adsk.core.CommandCreatedEventArgs):
        global _active_ui_builder, _edited_custom_feature
        try:
            cmd = args.command
            cmd.isRepeatable = False

            # 1. Locate the CustomFeature being edited
            _edited_custom_feature = _get_target_custom_feature()
            if not _edited_custom_feature:
                if _ui:
                    _ui.messageBox("Could not locate the CustomFeature to edit.")
                return

            # Register lifecycle handlers
            on_input_changed = InputChangedHandler()
            cmd.inputChanged.add(on_input_changed)
            _handlers.append(on_input_changed)

            on_validate = ValidateInputsHandler()
            cmd.validateInputs.add(on_validate)
            _handlers.append(on_validate)

            on_preview = EditExecutePreviewHandler()
            cmd.executePreview.add(on_preview)
            _handlers.append(on_preview)

            on_execute = EditExecuteHandler()
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)

            on_activate = EditActivateHandler()
            cmd.activate.add(on_activate)
            _handlers.append(on_activate)

            on_destroy = EditDestroyHandler()
            cmd.destroy.add(on_destroy)
            _handlers.append(on_destroy)

            # Build dialog inputs dynamically from declarative schema
            current_dir = os.path.dirname(os.path.realpath(__file__))
            schema_path = os.path.join(current_dir, 'ui_schema.json')

            _active_ui_builder = SchemaUIBuilder(cmd.commandInputs, schema_path, self.resource_dir)

            # 2. Retrieve saved configuration and dependent hole face
            params = _extract_custom_feature_config(_edited_custom_feature)
            _sync_params_from_custom_feature(_edited_custom_feature, params)

            target_body = _find_target_body_for_feature(_edited_custom_feature)
            hole_face = _find_matching_cylinder_face(target_body, params) if target_body else None

            # 3. Pre-populate dialog inputs with existing settings
            _active_ui_builder.populate_from_parameters(params, hole_face)
            if hole_face:
                _update_hole_info(_active_ui_builder)

        except:
            if _ui:
                _ui.messageBox(f"Error in EditCommandCreated:\n{traceback.format_exc()}")


def _get_target_custom_feature() -> adsk.fusion.CustomFeature:
    """Finds the CustomFeature associated with the current timeline selection or edit context."""
    if _ui and _ui.activeSelections.count > 0:
        for i in range(_ui.activeSelections.count):
            ent = _ui.activeSelections.item(i).entity
            if isinstance(ent, adsk.fusion.CustomFeature):
                return ent
            if hasattr(ent, 'entity') and isinstance(ent.entity, adsk.fusion.CustomFeature):
                return ent.entity

    design = adsk.fusion.Design.cast(_app.activeProduct) if _app else None
    if design and design.timeline:
        marker_idx = design.timeline.markerPosition - 1
        if 0 <= marker_idx < design.timeline.count:
            tl_ent = design.timeline.item(marker_idx).entity
            if isinstance(tl_ent, adsk.fusion.CustomFeature):
                return tl_ent

    return None


def _extract_custom_feature_config(cf: adsk.fusion.CustomFeature) -> dict:
    """Extracts JSON metadata config dict from custom feature named values or attributes."""
    if not cf or not cf.isValid:
        return {}

    config_str = None
    try:
        if cf.customNamedValues.isExistingValue('config'):
            config_str = cf.customNamedValues.value('config')
    except:
        pass

    if not config_str:
        try:
            attr = cf.attributes.itemByName('CrushFins', 'config')
            if attr:
                config_str = attr.value
        except:
            pass

    if config_str:
        try:
            return json.loads(config_str)
        except:
            pass

    return {}
 
 
def _find_target_body_for_feature(cf: adsk.fusion.CustomFeature) -> adsk.fusion.BRepBody:
    """Locates the primary target body associated with this CustomFeature."""
    if not cf or not cf.isValid:
        return None
    config = _extract_custom_feature_config(cf)
    body_name = config.get('body_name')
    parent_comp = cf.parentComponent
    if parent_comp:
        if body_name:
            for b in parent_comp.bRepBodies:
                if b.name == body_name and b.isValid:
                    return b
        if parent_comp.bRepBodies.count > 0:
            return parent_comp.bRepBodies.item(0)
    for feat in cf.features:
        if isinstance(feat, adsk.fusion.CombineFeature):
            try:
                tb = feat.targetBody
                if tb and tb.isValid:
                    return tb
            except:
                pass
    return None


def _find_matching_cylinder_face(body: adsk.fusion.BRepBody, params: dict) -> adsk.fusion.BRepFace:
    """Finds the original cylindrical hole face on target_body using saved geometric anchor."""
    if not body or not body.isValid:
        return None
    origin_pt = params.get('cyl_origin')
    axis_vec = params.get('cyl_axis')
    radius = params.get('cyl_radius')

    if origin_pt and axis_vec and radius:
        ref_origin = adsk.core.Point3D.create(origin_pt[0], origin_pt[1], origin_pt[2])
        ref_axis = adsk.core.Vector3D.create(axis_vec[0], axis_vec[1], axis_vec[2])
        best_face = None
        min_dist = float('inf')
        for f in body.faces:
            geom = f.geometry
            if geom.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType:
                cyl: adsk.core.Cylinder = geom
                if abs(cyl.axis.dotProduct(ref_axis)) > 0.85:
                    d = cyl.origin.distanceTo(ref_origin)
                    if abs(cyl.radius - radius) < 0.1 and d < min_dist:
                        min_dist = d
                        best_face = f
        if best_face:
            return best_face

    # Fallback to any cylindrical face
    for f in body.faces:
        if f.geometry.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType:
            return f
    return None


def _update_hole_info(ui_builder: SchemaUIBuilder):
    """Computes hole dimensions and updates the info display readout."""
    hole_sel = ui_builder.get_item('holeSelection')
    if not hole_sel or hole_sel.selectionCount == 0:
        ui_builder.set_formatted_text('infoBox', 'Please select a cylindrical hole face.')
        return

    try:
        face = adsk.fusion.BRepFace.cast(hole_sel.selection(0).entity)
        if not face or not face.isValid:
            return

        params = ui_builder.get_feature_parameters()
        flip = params.get('flip_direction', False)
        cyl_data = CylinderData(face, flip)

        dia_mm = cyl_data.diameter * 10.0
        len_mm = cyl_data.length * 10.0
        mode = params.get('type')

        if mode == TYPE_COMPLIANT_FINS:
            prot_mm = params.get('protrusion', 0.1) * 10.0
            inscribed_pin_mm = max(0.0, dia_mm - 2.0 * prot_mm)
            info_text = (
                f"<b>Hole:</b> &Oslash; {dia_mm:.2f} mm &times; {len_mm:.2f} mm<br/>"
                f"<b>Inscribed Pin:</b> &Oslash; {inscribed_pin_mm:.2f} mm (Fin reach: {prot_mm:.2f} mm / side)"
            )
        elif mode == TYPE_COATHANGER:
            wall_mm = params.get('wall_thickness', 0.12) * 10.0
            pin_mm = max(0.0, dia_mm - 2.0 * wall_mm)
            info_text = (
                f"<b>Hole:</b> &Oslash; {dia_mm:.2f} mm &times; {len_mm:.2f} mm<br/>"
                f"<b>Nominal Pin:</b> &Oslash; {pin_mm:.2f} mm (Finger thickness: {wall_mm:.2f} mm)"
            )
        else:
            h_mm = params.get('height', 0.03) * 10.0
            pin_mm = max(0.0, dia_mm - 2.0 * h_mm)
            info_text = (
                f"<b>Hole:</b> &Oslash; {dia_mm:.2f} mm &times; {len_mm:.2f} mm<br/>"
                f"<b>Nominal Pin:</b> &Oslash; {pin_mm:.2f} mm (Interference: {h_mm:.2f} mm / side)"
            )

        ui_builder.set_formatted_text('infoBox', info_text)

        # Pre-seed custom length if currently zero
        fin_len_inp = ui_builder.get_item('finLength')
        if fin_len_inp and fin_len_inp.value <= 0.001:
            fin_len_inp.value = cyl_data.length

    except:
        pass


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args: adsk.core.InputChangedEventArgs):
        global _active_ui_builder
        try:
            if not _active_ui_builder:
                return

            changed_input = args.input

            # 1. Update declarative visibility rules
            _active_ui_builder.on_input_changed(changed_input)

            # 2. Update dynamic hole info on selection or geometric parameter change
            trigger_ids = (
                'holeSelection', 'featureType',
                'finProtrusion', 'finFlip',
                'coatWallThickness',
                'crushHeight', 'crushFlip'
            )
            if changed_input.id in trigger_ids:
                _update_hole_info(_active_ui_builder)

        except:
            if _ui:
                _ui.messageBox(f"Error in InputChanged:\n{traceback.format_exc()}")


class ValidateInputsHandler(adsk.core.ValidateInputsEventHandler):
    def notify(self, args: adsk.core.ValidateInputsEventArgs):
        global _active_ui_builder
        try:
            if not _active_ui_builder:
                args.areInputsValid = False
                return

            hole_sel = _active_ui_builder.get_item('holeSelection')
            if not hole_sel or hole_sel.selectionCount == 0:
                args.areInputsValid = False
                return

            mode = _active_ui_builder.get_selected_feature_type()
            if mode == TYPE_COMPLIANT_FINS:
                thick = _active_ui_builder.get_value('finThickness')
                prot = _active_ui_builder.get_value('finProtrusion')
                if (thick is not None and thick <= 0.001) or (prot is not None and prot <= 0.001):
                    args.areInputsValid = False
                    return
            elif mode == TYPE_COATHANGER:
                wall = _active_ui_builder.get_value('coatWallThickness')
                slot = _active_ui_builder.get_value('coatSlotWidth')
                if (wall is not None and wall <= 0.001) or (slot is not None and slot <= 0.001):
                    args.areInputsValid = False
                    return
            elif mode == TYPE_CRUSH_RIBS:
                w = _active_ui_builder.get_value('crushWidth')
                h = _active_ui_builder.get_value('crushHeight')
                if (w is not None and w <= 0.001) or (h is not None and h <= 0.001):
                    args.areInputsValid = False
                    return

            args.areInputsValid = True
        except:
            args.areInputsValid = False


def _bind_custom_parameters(cf_input: adsk.fusion.CustomFeatureInput, params: dict, length_unit: str = 'mm'):
    """Registers exposed dimensional parameters into Fusion's Change Parameters (fx) table."""
    mode = params.get('type', TYPE_COMPLIANT_FINS)

    if mode == TYPE_COMPLIANT_FINS:
        cf_input.addCustomParameter('finCount', 'Fin Count', adsk.core.ValueInput.createByReal(float(params.get('count', 4))), '', True)
        cf_input.addCustomParameter('finThickness', 'Tip Thickness', adsk.core.ValueInput.createByReal(params.get('thickness', 0.08)), length_unit, True)
        cf_input.addCustomParameter('finRootThickness', 'Root Thickness', adsk.core.ValueInput.createByReal(params.get('thickness_root', 0.12)), length_unit, True)
        cf_input.addCustomParameter('finProtrusion', 'Fin Reach', adsk.core.ValueInput.createByReal(params.get('protrusion', 0.1)), length_unit, True)
        cf_input.addCustomParameter('finSlant', 'Slant Angle', adsk.core.ValueInput.createByReal(math.radians(params.get('slant_angle_deg', 45.0))), 'deg', True)
        if not params.get('full_depth', True):
            cf_input.addCustomParameter('finLength', 'Fin Length', adsk.core.ValueInput.createByReal(params.get('length', 1.0)), length_unit, True)
            cf_input.addCustomParameter('finOffset', 'Fin Offset', adsk.core.ValueInput.createByReal(params.get('offset', 0.0)), length_unit, True)
        if params.get('has_lead_in', True):
            cf_input.addCustomParameter('finLeadInLength', 'Lead-in Length', adsk.core.ValueInput.createByReal(params.get('lead_in_length', 0.15)), length_unit, True)
        if params.get('has_fillet', True):
            cf_input.addCustomParameter('finFilletRadius', 'Fillet Radius', adsk.core.ValueInput.createByReal(params.get('fillet_radius', 0.05)), length_unit, True)

    elif mode == TYPE_COATHANGER:
        cf_input.addCustomParameter('coatWallThickness', 'Finger Thickness', adsk.core.ValueInput.createByReal(params.get('wall_thickness', 0.12)), length_unit, True)
        cf_input.addCustomParameter('coatSlotWidth', 'Relief Slot Width', adsk.core.ValueInput.createByReal(params.get('slot_width', 0.15)), length_unit, True)
        cf_input.addCustomParameter('coatArcSpan', 'Arc Span', adsk.core.ValueInput.createByReal(math.radians(params.get('arc_deg', 120.0))), 'deg', True)
        cf_input.addCustomParameter('coatSlitWidth', 'Slit Width', adsk.core.ValueInput.createByReal(params.get('slit_width', 0.1)), length_unit, True)
        cf_input.addCustomParameter('coatRotationAngle', 'Orientation Angle', adsk.core.ValueInput.createByReal(math.radians(params.get('rotation_deg', 0.0))), 'deg', True)

    elif mode == TYPE_CRUSH_RIBS:
        cf_input.addCustomParameter('crushCount', 'Rib Count', adsk.core.ValueInput.createByReal(float(params.get('count', 4))), '', True)
        cf_input.addCustomParameter('crushWidth', 'Rib Width', adsk.core.ValueInput.createByReal(params.get('width', 0.08)), length_unit, True)
        cf_input.addCustomParameter('crushHeight', 'Rib Height', adsk.core.ValueInput.createByReal(params.get('height', 0.03)), length_unit, True)
        if not params.get('full_depth', True):
            cf_input.addCustomParameter('crushLength', 'Rib Length', adsk.core.ValueInput.createByReal(params.get('length', 1.0)), length_unit, True)
        if params.get('has_lead_in', True):
            cf_input.addCustomParameter('crushLeadInLength', 'Lead-in Length', adsk.core.ValueInput.createByReal(params.get('lead_in_length', 0.15)), length_unit, True)


def _sync_params_from_custom_feature(custom_feat: adsk.fusion.CustomFeature, params: dict):
    """Overrides params dictionary with live values from CustomFeatureParameters."""
    if not custom_feat or not custom_feat.isValid:
        return
    for p in custom_feat.parameters:
        try:
            pid = p.id
            if pid in ('finCount', 'crushCount'):
                params['count'] = int(round(p.value))
            elif pid == 'finThickness':
                params['thickness'] = p.value
            elif pid == 'finRootThickness':
                params['thickness_root'] = p.value
            elif pid == 'finProtrusion':
                params['protrusion'] = p.value
            elif pid == 'finSlant':
                params['slant_angle_deg'] = math.degrees(p.value)
            elif pid in ('finLength', 'crushLength'):
                params['length'] = p.value
            elif pid == 'finOffset':
                params['offset'] = p.value
            elif pid in ('finLeadInLength', 'crushLeadInLength'):
                params['lead_in_length'] = p.value
            elif pid == 'finFilletRadius':
                params['fillet_radius'] = p.value
            elif pid == 'coatWallThickness':
                params['wall_thickness'] = p.value
            elif pid == 'coatSlotWidth':
                params['slot_width'] = p.value
            elif pid == 'coatArcSpan':
                params['arc_deg'] = math.degrees(p.value)
            elif pid == 'coatSlitWidth':
                params['slit_width'] = p.value
            elif pid == 'coatRotationAngle':
                params['rotation_deg'] = math.degrees(p.value)
            elif pid == 'crushWidth':
                params['width'] = p.value
            elif pid == 'crushHeight':
                params['height'] = p.value
        except:
            pass


def _update_feature_parameters(custom_feat: adsk.fusion.CustomFeature, params: dict):
    """Updates expressions and values on existing CustomFeatureParameters during Edit."""
    if not custom_feat or not custom_feat.isValid:
        return
    for p in custom_feat.parameters:
        try:
            pid = p.id
            if pid in ('finCount', 'crushCount') and 'count' in params:
                p.value = float(params['count'])
            elif pid == 'finThickness' and 'thickness' in params:
                p.value = params['thickness']
            elif pid == 'finRootThickness' and 'thickness_root' in params:
                p.value = params['thickness_root']
            elif pid == 'finProtrusion' and 'protrusion' in params:
                p.value = params['protrusion']
            elif pid == 'finSlant' and 'slant_angle_deg' in params:
                p.value = math.radians(params['slant_angle_deg'])
            elif pid in ('finLength', 'crushLength') and 'length' in params:
                p.value = params['length']
            elif pid == 'finOffset' and 'offset' in params:
                p.value = params['offset']
            elif pid in ('finLeadInLength', 'crushLeadInLength') and 'lead_in_length' in params:
                p.value = params['lead_in_length']
            elif pid == 'finFilletRadius' and 'fillet_radius' in params:
                p.value = params['fillet_radius']
            elif pid == 'coatWallThickness' and 'wall_thickness' in params:
                p.value = params['wall_thickness']
            elif pid == 'coatSlotWidth' and 'slot_width' in params:
                p.value = params['slot_width']
            elif pid == 'coatArcSpan' and 'arc_deg' in params:
                p.value = math.radians(params['arc_deg'])
            elif pid == 'coatSlitWidth' and 'slit_width' in params:
                p.value = params['slit_width']
            elif pid == 'coatRotationAngle' and 'rotation_deg' in params:
                p.value = math.radians(params['rotation_deg'])
            elif pid == 'crushWidth' and 'width' in params:
                p.value = params['width']
            elif pid == 'crushHeight' and 'height' in params:
                p.value = params['height']
        except:
            pass


def _commit_feature(ui_builder: SchemaUIBuilder, is_preview: bool = False) -> bool:
    """Generates feature geometry into the model, registering a CustomFeature when in parametric mode."""
    if not ui_builder:
        return False

    params = ui_builder.get_feature_parameters()
    hole_selection = ui_builder.get_item('holeSelection')
    face = None
    if hole_selection and hole_selection.selectionCount > 0:
        entity = hole_selection.selection(0).entity
        if entity and entity.isValid:
            face = adsk.fusion.BRepFace.cast(entity)

    if not face or not face.isValid:
        # Fallback to finding matching cylinder face using saved geometric anchor
        design = adsk.fusion.Design.cast(_app.activeProduct) if _app else None
        root = design.rootComponent if design else None
        target_body = None
        if root:
            body_name = params.get('body_name')
            if body_name:
                for b in root.bRepBodies:
                    if b.name == body_name and b.isValid:
                        target_body = b
                        break
            if not target_body and root.bRepBodies.count > 0:
                target_body = root.bRepBodies.item(0)

        if target_body:
            face = _find_matching_cylinder_face(target_body, params)
            if face and face.isValid and hole_selection:
                try:
                    hole_selection.clear()
                    hole_selection.addSelection(face)
                except:
                    pass

    if not face or not face.isValid:
        return False

    target_body = face.body
    parent_component = target_body.parentComponent
    design = parent_component.parentDesign
    is_parametric = (design.designType == adsk.fusion.DesignTypes.ParametricDesignType)

    mode = params.get('type', TYPE_COMPLIANT_FINS)
    flip = bool(params.get('flip_direction', False))
    cyl_data = CylinderData(face, flip)

    # 1. Generate Tool / Cutter B-Rep Body
    if mode == TYPE_COMPLIANT_FINS:
        tool_body = create_compliant_fins_body(cyl_data, params)
        if not tool_body or not tool_body.isValid:
            return False
        feat_res = apply_tool_body(parent_component, target_body, tool_body, "Compliant Fins", cyl_data, params, create_group=not is_parametric)

    elif mode == TYPE_COATHANGER:
        cutter_body = create_coathanger_cutter(cyl_data, params)
        if not cutter_body or not cutter_body.isValid:
            return False
        feat_res = apply_cutter_body(parent_component, target_body, cutter_body, "Coathanger Flexure", create_group=not is_parametric)

    elif mode == TYPE_CRUSH_RIBS:
        tool_body = create_crush_ribs_body(cyl_data, params)
        if not tool_body or not tool_body.isValid:
            return False
        feat_res = apply_tool_body(parent_component, target_body, tool_body, "Crush Ribs", cyl_data, params, create_group=not is_parametric)

    else:
        return False

    if not feat_res:
        return False

    # 2. Preview mode does not instantiate a persistent CustomFeature
    if is_preview:
        return True

    # 3. Parametric Mode: Bind into CustomFeature
    if is_parametric and _custom_feature_def:
        try:
            custom_feats = parent_component.features.customFeatures
            cf_input = custom_feats.createInput(_custom_feature_def)
            cf_input.setStartAndEndFeatures(feat_res['base_feature'], feat_res['end_feature'])

            length_unit = design.unitsManager.defaultLengthUnits if design else 'mm'
            _bind_custom_parameters(cf_input, params, length_unit)

            cf = custom_feats.add(cf_input)
            if not cf:
                return False

            cf.name = mode

            # Save geometric cylinder anchor in params
            params['cyl_origin'] = [cyl_data.origin.x, cyl_data.origin.y, cyl_data.origin.z]
            params['cyl_axis'] = [cyl_data.axis.x, cyl_data.axis.y, cyl_data.axis.z]
            params['cyl_radius'] = cyl_data.radius
            params['cyl_length'] = cyl_data.length
            params['body_name'] = target_body.name

            # Persist full JSON config
            params_json = json.dumps(params)
            cf.customNamedValues.addOrSetValue('config', params_json)
            try:
                cf.attributes.add('CrushFins', 'config', params_json)
            except:
                pass

            return True
        except:
            if _ui:
                _ui.messageBox(f"Error binding CustomFeature:\n{traceback.format_exc()}")
            return False

    return True


class ExecutePreviewHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _active_ui_builder
        try:
            preview_box = _active_ui_builder.get_item('showPreview') if _active_ui_builder else None
            if preview_box and not preview_box.value:
                return

            _commit_feature(_active_ui_builder, is_preview=True)
            args.isValidResult = False
        except:
            args.isValidResult = False


class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _active_ui_builder
        try:
            if not _commit_feature(_active_ui_builder, is_preview=False):
                args.executeFailed = True
                args.executeFailedMessage = "Could not generate feature. Please verify parameters."
        except Exception as e:
            args.executeFailed = True
            args.executeFailedMessage = f"Operation failed:\n{traceback.format_exc()}"


class DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _active_ui_builder
        _active_ui_builder = None


class EditActivateHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _restore_timeline_object, _is_rolled_for_edit, _edited_custom_feature
        try:
            if not _edited_custom_feature or not _edited_custom_feature.isValid:
                return

            design = adsk.fusion.Design.cast(_app.activeProduct)
            if not design:
                return

            # Save marker position and roll timeline to just before the feature
            timeline = design.timeline
            _restore_timeline_object = timeline.item(timeline.markerPosition - 1)
            _edited_custom_feature.timelineObject.rollTo(rollBefore=True)
            _is_rolled_for_edit = True
            args.command.beginStep()

            # Ensure face is selected in UI at rolled-before state
            config = _extract_custom_feature_config(_edited_custom_feature)
            target_body = _find_target_body_for_feature(_edited_custom_feature)
            face = _find_matching_cylinder_face(target_body, config) if target_body else None
            if face and face.isValid and _active_ui_builder:
                hole_sel = _active_ui_builder.get_item('holeSelection')
                if hole_sel and hole_sel.selectionCount == 0:
                    hole_sel.addSelection(face)
        except:
            pass


class EditExecutePreviewHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _active_ui_builder
        try:
            preview_box = _active_ui_builder.get_item('showPreview') if _active_ui_builder else None
            if preview_box and not preview_box.value:
                args.isValidResult = False
                return

            _commit_feature(_active_ui_builder, is_preview=True)
            args.isValidResult = False
        except:
            args.isValidResult = False


class EditExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _edited_custom_feature, _is_rolled_for_edit, _restore_timeline_object
        try:
            if not _edited_custom_feature or not _edited_custom_feature.isValid:
                return

            design = adsk.fusion.Design.cast(_app.activeProduct) if _app else None

            params = _active_ui_builder.get_feature_parameters() if _active_ui_builder else None
            if not params:
                return

            old_params = _extract_custom_feature_config(_edited_custom_feature)
            old_mode = old_params.get('type')
            new_mode = params.get('type')
            old_has_fillet = bool(old_params.get('has_fillet', False))
            new_has_fillet = bool(params.get('has_fillet', False))

            if (old_mode and new_mode and old_mode != new_mode) or (old_has_fillet != new_has_fillet):
                # Type or fillet topology switched - unroll and recreate CustomFeature cleanly
                if _is_rolled_for_edit:
                    if _restore_timeline_object and _restore_timeline_object.isValid:
                        try:
                            _restore_timeline_object.rollTo(False)
                        except:
                            if design:
                                design.timeline.moveToEnd()
                    elif design:
                        design.timeline.moveToEnd()
                    _is_rolled_for_edit = False

                _edited_custom_feature.deleteMe()
                _edited_custom_feature = None
                if not _commit_feature(_active_ui_builder, is_preview=False):
                    args.executeFailed = True
                    args.executeFailedMessage = "Failed to recreate feature after type switch."
                return

            # 1. Update CustomFeature parameters
            _update_feature_parameters(_edited_custom_feature, params)

            # 2. Get re-selected or matched hole face
            hole_sel = _active_ui_builder.get_item('holeSelection') if _active_ui_builder else None
            face = hole_sel.selection(0).entity if (hole_sel and hole_sel.selectionCount > 0) else None
            if not face or not face.isValid:
                target_body = _find_target_body_for_feature(_edited_custom_feature)
                face = _find_matching_cylinder_face(target_body, params) if target_body else None

            if not face or not face.isValid:
                args.executeFailed = True
                args.executeFailedMessage = "Target hole face is missing or invalid."
                return

            flip = bool(params.get('flip_direction', False))
            cyl_data = CylinderData(face, flip)

            # Update geometric anchor in params
            params['cyl_origin'] = [cyl_data.origin.x, cyl_data.origin.y, cyl_data.origin.z]
            params['cyl_axis'] = [cyl_data.axis.x, cyl_data.axis.y, cyl_data.axis.z]
            params['cyl_radius'] = cyl_data.radius
            params['cyl_length'] = cyl_data.length
            params['body_name'] = face.body.name

            # 3. Update geometry in-place
            ok = update_custom_feature_geometry(_edited_custom_feature, face.body, cyl_data, params)
            if not ok:
                args.executeFailed = True
                args.executeFailedMessage = "Failed to update custom feature geometry."
                return

            # Update feature name if feature type changed
            mode = params.get('type')
            if mode:
                _edited_custom_feature.name = mode

            # 4. Save metadata
            params_json = json.dumps(params)
            _edited_custom_feature.customNamedValues.addOrSetValue('config', params_json)
            try:
                _edited_custom_feature.attributes.add('CrushFins', 'config', params_json)
            except:
                pass

            # 5. Restore timeline roll position
            if _is_rolled_for_edit:
                if _restore_timeline_object and _restore_timeline_object.isValid:
                    try:
                        _restore_timeline_object.rollTo(False)
                    except:
                        if design:
                            design.timeline.moveToEnd()
                elif design:
                    design.timeline.moveToEnd()
                _is_rolled_for_edit = False

            _edited_custom_feature = None

        except Exception as e:
            args.executeFailed = True
            args.executeFailedMessage = f"Edit failed:\n{traceback.format_exc()}"


class EditDestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _active_ui_builder, _edited_custom_feature, _is_rolled_for_edit, _restore_timeline_object
        _active_ui_builder = None
        if _is_rolled_for_edit:
            design = adsk.fusion.Design.cast(_app.activeProduct) if _app else None
            if _restore_timeline_object and _restore_timeline_object.isValid:
                try:
                    _restore_timeline_object.rollTo(False)
                except:
                    if design:
                        design.timeline.moveToEnd()
            elif design:
                design.timeline.moveToEnd()
            _is_rolled_for_edit = False
        _edited_custom_feature = None


class ComputeCustomFeatureHandler(adsk.fusion.CustomFeatureEventHandler):
    def notify(self, args: adsk.fusion.CustomFeatureEventArgs):
        """Called automatically by Fusion when upstream timeline changes require recomputing this CustomFeature."""
        try:
            cf = args.customFeature
            if not cf or not cf.isValid:
                return

            params = _extract_custom_feature_config(cf)
            if not params:
                return

            target_body = _find_target_body_for_feature(cf)
            if not target_body or not target_body.isValid:
                args.computeStatus.statusMessages.addError('TARGET_BODY_LOST', 'Target body lost or deleted.')
                return

            face = _find_matching_cylinder_face(target_body, params)
            if not face or not face.isValid:
                args.computeStatus.statusMessages.addError('HOLE_FACE_LOST', 'Hole face could not be located on target body.')
                return

            _sync_params_from_custom_feature(cf, params)
            flip = bool(params.get('flip_direction', False))
            cyl_data = CylinderData(face, flip)

            update_custom_feature_geometry(cf, face.body, cyl_data, params)
        except:
            pass


def run(context):
    """Add-in startup."""
    global _app, _ui, _handlers, _custom_feature_def
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        current_dir = os.path.dirname(os.path.realpath(__file__))
        resource_dir = os.path.join(current_dir, 'resources')

        stop(context)

        cmd_defs = _ui.commandDefinitions

        # 1. Register Edit Command Definition
        edit_cmd_def = cmd_defs.itemById(EDIT_COMMAND_ID)
        if not edit_cmd_def:
            edit_cmd_def = cmd_defs.addButtonDefinition(
                EDIT_COMMAND_ID,
                EDIT_COMMAND_NAME,
                COMMAND_DESCRIPTION,
                resource_dir
            )
        edit_created_handler = EditCommandCreatedHandler(resource_dir)
        edit_cmd_def.commandCreated.add(edit_created_handler)
        _handlers.append(edit_created_handler)

        # 2. Register CustomFeatureDefinition
        _custom_feature_def = adsk.fusion.CustomFeatureDefinition.create(
            CUSTOM_FEATURE_DEF_ID,
            CUSTOM_FEATURE_NAME,
            resource_dir
        )
        _custom_feature_def.editCommandId = EDIT_COMMAND_ID
        try:
            _custom_feature_def.isRollTimeline = False
        except:
            pass

        compute_handler = ComputeCustomFeatureHandler()
        _custom_feature_def.customFeatureCompute.add(compute_handler)
        _handlers.append(compute_handler)

        # 3. Register Creation Command Definition
        cmd_def = cmd_defs.addButtonDefinition(
            COMMAND_ID,
            COMMAND_NAME,
            COMMAND_DESCRIPTION,
            resource_dir
        )
        cmd_def.toolClipFilename = os.path.join(resource_dir, '32x32@2x.png')

        created_handler = CommandCreatedHandler(resource_dir)
        cmd_def.commandCreated.add(created_handler)
        _handlers.append(created_handler)

        # 4. Add Command to Fusion Toolbar Panel
        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            control = panel.controls.addCommand(cmd_def)
            control.isPromoted = True
            control.isPromotedByDefault = True

    except:
        if _ui:
            _ui.messageBox(f"Failed to load Wiggly Hole Generator Add-In:\n{traceback.format_exc()}")


def stop(context):
    """Add-in cleanup."""
    global _app, _ui, _handlers, _active_ui_builder, _custom_feature_def
    try:
        if not _ui:
            _app = adsk.core.Application.get()
            _ui = _app.userInterface if _app else None

        if _ui:
            panel = _ui.allToolbarPanels.itemById(PANEL_ID)
            for cid in (COMMAND_ID, LEGACY_COMMAND_ID, EDIT_COMMAND_ID):
                if panel:
                    ctrl = panel.controls.itemById(cid)
                    if ctrl:
                        ctrl.deleteMe()

                cmd_def = _ui.commandDefinitions.itemById(cid)
                if cmd_def:
                    cmd_def.deleteMe()

        _handlers.clear()
        _active_ui_builder = None
        _custom_feature_def = None
    except:
        pass
