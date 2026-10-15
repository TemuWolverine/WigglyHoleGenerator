"""
Wiggly Hole Generator - Autodesk Fusion 360 Add-In
Automated generation of Compliant Flexural Fins, Coathanger Flexures & Crush Ribs inside cylindrical holes.
"""

import os
import math
import traceback
import adsk.core
import adsk.fusion

try:
    from .geometry import (
        CylinderData, FinDirection, create_compliant_fins_body,
        create_coathanger_cutter, apply_tool_body, apply_cutter_body
    )
except ImportError:
    from geometry import (
        CylinderData, FinDirection, create_compliant_fins_body,
        create_coathanger_cutter, apply_tool_body, apply_cutter_body
    )

_app = None
_ui = None
_handlers = []

COMMAND_ID = 'WigglyHoleGeneratorCommand'
LEGACY_COMMAND_ID = 'CrushFinsCommand'
COMMAND_NAME = 'Wiggly Hole Generator'
COMMAND_DESCRIPTION = 'Generate Compliant Flexural Fins, Coathanger Flexures, or Crush Ribs inside cylindrical holes.'
PANEL_ID = 'SolidCreatePanel'

TYPE_COMPLIANT_FINS = 'Compliant Fins'
TYPE_COATHANGER = 'Coathanger'
TYPE_CRUSH_RIBS = 'Crush Ribs'


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, resource_dir):
        super().__init__()
        self.resource_dir = resource_dir

    def notify(self, args: adsk.core.CommandCreatedEventArgs):
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

            # Build dialog inputs
            inputs = cmd.commandInputs
            design = adsk.fusion.Design.cast(_app.activeProduct)
            units_mgr = design.unitsManager if design else None
            length_unit = units_mgr.defaultLengthUnits if units_mgr else 'mm'

            # 1. Hole Face Selection Input
            hole_selection = inputs.addSelectionInput(
                'holeSelection', 'Hole Face', 'Select cylindrical face of the hole'
            )
            hole_selection.addSelectionFilter('CylindricalFaces')
            hole_selection.setSelectionLimits(1, 1)
            hole_selection.tooltip = 'Select the cylindrical interior face of the hole.'

            # 2. Mode Selector: Segmented Button Row
            crush_icon_dir = os.path.join(self.resource_dir, 'ui', 'icon_crush_ribs').replace('\\', '/')
            fin_icon_dir = os.path.join(self.resource_dir, 'ui', 'icon_compliant_fins').replace('\\', '/')
            coat_icon_dir = os.path.join(self.resource_dir, 'ui', 'icon_coathanger').replace('\\', '/')

            feature_type_row = inputs.addButtonRowCommandInput('featureType', 'Type', False)
            feature_type_row.listItems.add(
                TYPE_COMPLIANT_FINS,
                True,
                fin_icon_dir
            )
            feature_type_row.listItems.add(
                TYPE_COATHANGER,
                False,
                coat_icon_dir
            )
            feature_type_row.listItems.add(
                TYPE_CRUSH_RIBS,
                False,
                crush_icon_dir
            )

            # 3. Dynamic Hole / Pin Info Readout
            info_box = inputs.addTextBoxCommandInput('infoBox', '', 'Please select a cylindrical hole face.', 2, True)
            info_box.isFullWidth = True

            # =====================================================================
            # 4. Compliant Fins Parameter Group
            # =====================================================================
            fin_group = inputs.addGroupCommandInput('finGroup', 'Compliant Fins Parameters')
            fin_group.isExpanded = True
            f_inputs = fin_group.children

            fin_count = f_inputs.addIntegerSpinnerCommandInput('finCount', 'Fin Count', 2, 16, 1, 4)
            fin_count.tooltip = 'Number of flexural fins evenly spaced around the hole circumference.'

            fin_thickness = f_inputs.addValueInput(
                'finThickness', 'Tip Thickness', length_unit, adsk.core.ValueInput.createByString('0.8 mm')
            )
            fin_thickness.tooltip = 'Blade thickness at the free contact tip (e.g. 2 perimeters for 3D printing).'

            fin_root_thickness = f_inputs.addValueInput(
                'finRootThickness', 'Root Thickness', length_unit, adsk.core.ValueInput.createByString('1.2 mm')
            )
            fin_root_thickness.tooltip = 'Blade thickness at the hole wall base. Tapering from root to tip forms a constant-stress beam.'

            fin_protrusion = f_inputs.addValueInput(
                'finProtrusion', 'Fin Reach (Inward)', length_unit, adsk.core.ValueInput.createByString('1.0 mm')
            )
            fin_protrusion.tooltip = 'Radial reach of the fin inward into the hole from the wall.'

            fin_slant = f_inputs.addValueInput(
                'finSlant', 'Slant Angle', 'deg', adsk.core.ValueInput.createByString('45 deg')
            )
            fin_slant.tooltip = 'Angle of the fin relative to the radial direction.'

            fin_direction = f_inputs.addDropDownCommandInput(
                'finDirection', 'Slant Direction', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            fin_direction.listItems.add(FinDirection.CLOCKWISE, True)
            fin_direction.listItems.add(FinDirection.COUNTER_CLOCKWISE, False)

            fin_fillet = f_inputs.addBoolValueInput('finFillet', 'Root Fillet', True, '', True)
            fin_fillet.tooltip = 'Add a fillet where the fins connect to the hole wall to relieve stress concentration.'

            fin_fillet_radius = f_inputs.addValueInput(
                'finFilletRadius', 'Fillet Radius', length_unit, adsk.core.ValueInput.createByString('0.5 mm')
            )
            fin_fillet_radius.tooltip = 'Radius of the fillet blending the fin root into the hole wall.'

            fin_full_depth = f_inputs.addBoolValueInput('finFullDepth', 'Full Depth', True, '', True)
            fin_full_depth.tooltip = 'Extend the fins along the entire axial length of the hole.'

            fin_length = f_inputs.addValueInput(
                'finLength', 'Custom Length', length_unit, adsk.core.ValueInput.createByString('10 mm')
            )
            fin_length.isVisible = False

            fin_offset = f_inputs.addValueInput(
                'finOffset', 'Start Offset', length_unit, adsk.core.ValueInput.createByString('0 mm')
            )
            fin_offset.isVisible = False

            fin_lead_in = f_inputs.addBoolValueInput('finLeadIn', 'Lead-in Chamfer', True, '', True)
            fin_lead_in.tooltip = 'Add an angled entrance chamfer so the pin centers and glides in smoothly.'

            fin_lead_in_length = f_inputs.addValueInput(
                'finLeadInLength', 'Lead-in Length', length_unit, adsk.core.ValueInput.createByString('1.5 mm')
            )

            fin_flip = f_inputs.addBoolValueInput('finFlip', 'Flip Entrance', True, '', False)
            fin_flip.tooltip = 'Invert the hole entrance direction.'

            # =====================================================================
            # 5. Coathanger Flexure Group
            # =====================================================================
            coat_group = inputs.addGroupCommandInput('coatGroup', 'Coathanger Flexure Parameters')
            coat_group.isExpanded = True
            coat_group.isVisible = False
            ct_inputs = coat_group.children

            ct_inputs.addValueInput(
                'coatWallThickness', 'Finger Thickness', length_unit, adsk.core.ValueInput.createByString('1.2 mm')
            )
            ct_inputs.addValueInput(
                'coatSlotWidth', 'Relief Slot Width', length_unit, adsk.core.ValueInput.createByString('1.5 mm')
            )
            ct_inputs.addValueInput(
                'coatArcSpan', 'Arc Span', 'deg', adsk.core.ValueInput.createByString('120 deg')
            )
            ct_inputs.addValueInput(
                'coatSlitWidth', 'Slit Width', length_unit, adsk.core.ValueInput.createByString('1.0 mm')
            )
            ct_inputs.addValueInput(
                'coatRotationAngle', 'Orientation Angle', 'deg', adsk.core.ValueInput.createByString('0 deg')
            )

            # =====================================================================
            # 6. Crush Ribs Group (Placeholder for Crush Ribs mode)
            # =====================================================================
            crush_group = inputs.addGroupCommandInput('crushGroup', 'Crush Ribs Parameters')
            crush_group.isExpanded = True
            crush_group.isVisible = False
            c_inputs = crush_group.children

            c_inputs.addIntegerSpinnerCommandInput('crushCount', 'Rib Count', 2, 16, 1, 4)
            c_inputs.addValueInput('crushWidth', 'Fin / Rib Width', length_unit, adsk.core.ValueInput.createByString('0.8 mm'))
            c_inputs.addValueInput('crushHeight', 'Interference Height', length_unit, adsk.core.ValueInput.createByString('0.3 mm'))
            c_inputs.addValueInput('crushLength', 'Fin / Rib Length', length_unit, adsk.core.ValueInput.createByString('10 mm'))

            # 7. Preview Option
            inputs.addBoolValueInput('showPreview', 'Show Preview', True, '', True)

            # 8. Pre-Selection check
            if _ui and _ui.activeSelections.count > 0:
                pre_sel = _ui.activeSelections.item(0).entity
                if pre_sel and isinstance(pre_sel, adsk.fusion.BRepFace):
                    geom = pre_sel.geometry
                    if geom and geom.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType:
                        hole_selection.addSelection(pre_sel)
                        _update_hole_info(inputs, pre_sel, False)

        except:
            if _ui:
                _ui.messageBox(f"Error in CommandCreated:\n{traceback.format_exc()}")


def _update_hole_info(inputs: adsk.core.CommandInputs, face: adsk.fusion.BRepFace, flip: bool):
    """Computes hole dimensions and updates the info display and defaults."""
    try:
        cyl_data = CylinderData(face, flip)
        dia_mm = cyl_data.diameter * 10.0
        len_mm = cyl_data.length * 10.0

        fin_prot_input = inputs.itemById('finProtrusion')
        prot_cm = fin_prot_input.value if fin_prot_input else 0.1
        prot_mm = prot_cm * 10.0

        inscribed_pin_mm = max(0.0, dia_mm - 2.0 * prot_mm)
        info_box = inputs.itemById('infoBox')
        if info_box:
            info_box.formattedText = (
                f"<b>Hole:</b> &Oslash; {dia_mm:.2f} mm &times; {len_mm:.2f} mm<br/>"
                f"<b>Inscribed Pin:</b> &Oslash; {inscribed_pin_mm:.2f} mm (Fin reach: {prot_mm:.2f} mm / side)"
            )

        fin_len_input = inputs.itemById('finLength')
        if fin_len_input and fin_len_input.value <= 0.001:
            fin_len_input.value = cyl_data.length

    except:
        pass


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args: adsk.core.InputChangedEventArgs):
        try:
            inputs = args.inputs
            changed_input = args.input

            feature_type_row = inputs.itemById('featureType')
            crush_group = inputs.itemById('crushGroup')
            fin_group = inputs.itemById('finGroup')
            coat_group = inputs.itemById('coatGroup')
            hole_selection = inputs.itemById('holeSelection')
            info_box = inputs.itemById('infoBox')

            fin_full_depth = inputs.itemById('finFullDepth')
            fin_length = inputs.itemById('finLength')
            fin_offset = inputs.itemById('finOffset')
            fin_lead_in = inputs.itemById('finLeadIn')
            fin_lead_in_length = inputs.itemById('finLeadInLength')
            fin_flip = inputs.itemById('finFlip')
            fin_fillet = inputs.itemById('finFillet')
            fin_fillet_radius = inputs.itemById('finFilletRadius')

            # Handle Type Toggle
            if changed_input.id == 'featureType':
                sel_name = feature_type_row.selectedItem.name
                if fin_group:
                    fin_group.isVisible = (sel_name == TYPE_COMPLIANT_FINS)
                if coat_group:
                    coat_group.isVisible = (sel_name == TYPE_COATHANGER)
                if crush_group:
                    crush_group.isVisible = (sel_name == TYPE_CRUSH_RIBS)

            # Handle Hole Selection / Flip / Reach Update
            if changed_input.id in ('holeSelection', 'finFlip', 'finProtrusion'):
                if hole_selection and hole_selection.selectionCount > 0:
                    face = adsk.fusion.BRepFace.cast(hole_selection.selection(0).entity)
                    flip_val = fin_flip.value if fin_flip else False
                    _update_hole_info(inputs, face, flip_val)
                elif info_box:
                    info_box.formattedText = 'Please select a cylindrical hole face.'

            # Handle Full Depth toggle
            if changed_input.id == 'finFullDepth' and fin_full_depth:
                if fin_length:
                    fin_length.isVisible = not fin_full_depth.value
                if fin_offset:
                    fin_offset.isVisible = not fin_full_depth.value

            # Handle Lead-in Chamfer toggle
            if changed_input.id == 'finLeadIn' and fin_lead_in:
                if fin_lead_in_length:
                    fin_lead_in_length.isVisible = fin_lead_in.value

            # Handle Root Fillet toggle
            if changed_input.id == 'finFillet' and fin_fillet:
                if fin_fillet_radius:
                    fin_fillet_radius.isVisible = fin_fillet.value

        except:
            if _ui:
                _ui.messageBox(f"Error in InputChanged:\n{traceback.format_exc()}")


class ValidateInputsHandler(adsk.core.ValidateInputsEventHandler):
    def notify(self, args: adsk.core.ValidateInputsEventArgs):
        try:
            inputs = args.inputs
            hole_selection = inputs.itemById('holeSelection')
            if not hole_selection or hole_selection.selectionCount == 0:
                args.areInputsValid = False
                return

            feature_type = inputs.itemById('featureType').selectedItem.name

            if feature_type == TYPE_COMPLIANT_FINS:
                fin_thickness = inputs.itemById('finThickness')
                fin_protrusion = inputs.itemById('finProtrusion')
                if fin_thickness and fin_thickness.value <= 0.001:
                    args.areInputsValid = False
                    return
                if fin_protrusion and fin_protrusion.value <= 0.001:
                    args.areInputsValid = False
                    return
            elif feature_type == TYPE_COATHANGER:
                coat_wall = inputs.itemById('coatWallThickness')
                coat_slot = inputs.itemById('coatSlotWidth')
                if coat_wall and coat_wall.value <= 0.001:
                    args.areInputsValid = False
                    return
                if coat_slot and coat_slot.value <= 0.001:
                    args.areInputsValid = False
                    return

            args.areInputsValid = True
        except:
            args.areInputsValid = False


def _extract_fin_params(inputs: adsk.core.CommandInputs) -> dict:
    """Extracts all compliant fin parameters from dialog inputs with fallbacks."""
    count_inp = inputs.itemById('finCount')
    thick_inp = inputs.itemById('finThickness')
    root_thick_inp = inputs.itemById('finRootThickness')
    prot_inp = inputs.itemById('finProtrusion')
    slant_inp = inputs.itemById('finSlant')
    dir_inp = inputs.itemById('finDirection')
    full_depth_inp = inputs.itemById('finFullDepth')
    len_inp = inputs.itemById('finLength')
    off_inp = inputs.itemById('finOffset')
    lead_inp = inputs.itemById('finLeadIn')
    lead_len_inp = inputs.itemById('finLeadInLength')
    fillet_inp = inputs.itemById('finFillet')
    fillet_rad_inp = inputs.itemById('finFilletRadius')

    count = count_inp.value if count_inp else 4
    thickness = thick_inp.value if thick_inp else 0.08
    root_thickness = root_thick_inp.value if root_thick_inp else thickness * 1.5
    protrusion = prot_inp.value if prot_inp else 0.1
    slant_rad = slant_inp.value if slant_inp else math.radians(45.0)
    direction = dir_inp.selectedItem.name if (dir_inp and dir_inp.selectedItem) else FinDirection.CLOCKWISE
    full_depth = full_depth_inp.value if full_depth_inp else True
    length = len_inp.value if len_inp else 1.0
    offset = off_inp.value if off_inp else 0.0
    has_lead_in = lead_inp.value if lead_inp else True
    lead_in_length = lead_len_inp.value if lead_len_inp else 0.15
    has_fillet = fillet_inp.value if fillet_inp else True
    fillet_radius = fillet_rad_inp.value if fillet_rad_inp else 0.05

    return {
        'count': int(count),
        'thickness': float(thickness),
        'thickness_root': float(root_thickness),
        'protrusion': float(protrusion),
        'slant_angle_deg': float(math.degrees(slant_rad)),
        'direction': direction,
        'full_depth': bool(full_depth),
        'length': float(length),
        'offset': float(offset),
        'has_lead_in': bool(has_lead_in),
        'lead_in_length': float(lead_in_length),
        'has_fillet': bool(has_fillet),
        'fillet_radius': float(fillet_radius)
    }


def _commit_feature(inputs: adsk.core.CommandInputs) -> bool:
    """Commits feature geometry (Compliant Fins or Coathanger) merged directly into target body."""
    hole_selection = inputs.itemById('holeSelection')
    if not hole_selection or hole_selection.selectionCount == 0:
        return False

    entity = hole_selection.selection(0).entity
    face = adsk.fusion.BRepFace.cast(entity)
    if not face:
        return False

    target_body = face.body
    parent_component = target_body.parentComponent
    feature_type = inputs.itemById('featureType').selectedItem.name

    if feature_type == TYPE_COMPLIANT_FINS:
        fin_flip = inputs.itemById('finFlip').value if inputs.itemById('finFlip') else False
        cyl_data = CylinderData(face, fin_flip)
        params = _extract_fin_params(inputs)

        tool_body = create_compliant_fins_body(cyl_data, params)
        if not tool_body or not tool_body.isValid:
            return False

        apply_tool_body(parent_component, target_body, tool_body, "Compliant Fins", cyl_data, params)
        return True

    elif feature_type == TYPE_COATHANGER:
        cyl_data = CylinderData(face, False)
        wall_thick = inputs.itemById('coatWallThickness').value
        slot_width = inputs.itemById('coatSlotWidth').value
        arc_rad = inputs.itemById('coatArcSpan').value
        slit_width = inputs.itemById('coatSlitWidth').value
        rot_rad = inputs.itemById('coatRotationAngle').value

        params = {
            'wall_thickness': wall_thick,
            'slot_width': slot_width,
            'arc_deg': math.degrees(arc_rad),
            'slit_width': slit_width,
            'rotation_deg': math.degrees(rot_rad)
        }

        cutter_body = create_coathanger_cutter(cyl_data, params)
        if not cutter_body or not cutter_body.isValid:
            return False

        apply_cutter_body(parent_component, target_body, cutter_body, "Coathanger Flexure")
        return True

    return False


class ExecutePreviewHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        try:
            inputs = args.command.commandInputs
            preview_box = inputs.itemById('showPreview')
            if preview_box and not preview_box.value:
                return

            if _commit_feature(inputs):
                args.isValidResult = True
            else:
                args.isValidResult = False
        except:
            args.isValidResult = False


class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        try:
            inputs = args.command.commandInputs
            preview_box = inputs.itemById('showPreview')
            if not preview_box or not preview_box.value:
                if not _commit_feature(inputs):
                    args.executeFailed = True
                    args.executeFailedMessage = "Could not generate feature. Please verify parameters."
        except Exception as e:
            args.executeFailed = True
            args.executeFailedMessage = f"Operation failed:\n{traceback.format_exc()}"


class DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        pass


def run(context):
    """Add-in startup."""
    global _app, _ui, _handlers
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        current_dir = os.path.dirname(os.path.realpath(__file__))
        resource_dir = os.path.join(current_dir, 'resources')

        stop(context)

        cmd_defs = _ui.commandDefinitions
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
    global _app, _ui, _handlers
    try:
        if not _ui:
            _app = adsk.core.Application.get()
            _ui = _app.userInterface if _app else None

        if _ui:
            panel = _ui.allToolbarPanels.itemById(PANEL_ID)
            for cid in (COMMAND_ID, LEGACY_COMMAND_ID):
                if panel:
                    ctrl = panel.controls.itemById(cid)
                    if ctrl:
                        ctrl.deleteMe()

                cmd_def = _ui.commandDefinitions.itemById(cid)
                if cmd_def:
                    cmd_def.deleteMe()

        _handlers.clear()
    except:
        pass
