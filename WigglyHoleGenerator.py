"""
Wiggly Hole Generator - Autodesk Fusion 360 Add-In
Automated generation of Compliant Flexural Fins, Coathanger Flexures & Crush Ribs inside cylindrical holes.
Powered by a clean declarative JSON UI schema (ui_schema.json) for 100% native Fusion look and feel.
"""

import os
import math
import traceback
import adsk.core
import adsk.fusion

try:
    from .geometry import (
        CylinderData, FinDirection, create_compliant_fins_body,
        create_coathanger_cutter, create_crush_ribs_body,
        apply_tool_body, apply_cutter_body
    )
    from .ui_builder import SchemaUIBuilder
except ImportError:
    from geometry import (
        CylinderData, FinDirection, create_compliant_fins_body,
        create_coathanger_cutter, create_crush_ribs_body,
        apply_tool_body, apply_cutter_body
    )
    from ui_builder import SchemaUIBuilder

_app = None
_ui = None
_handlers = []
_active_ui_builder = None

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
            prot_mm = params['protrusion'] * 10.0
            inscribed_pin_mm = max(0.0, dia_mm - 2.0 * prot_mm)
            info_text = (
                f"<b>Hole:</b> &Oslash; {dia_mm:.2f} mm &times; {len_mm:.2f} mm<br/>"
                f"<b>Inscribed Pin:</b> &Oslash; {inscribed_pin_mm:.2f} mm (Fin reach: {prot_mm:.2f} mm / side)"
            )
        elif mode == TYPE_COATHANGER:
            wall_mm = params['wall_thickness'] * 10.0
            pin_mm = max(0.0, dia_mm - 2.0 * wall_mm)
            info_text = (
                f"<b>Hole:</b> &Oslash; {dia_mm:.2f} mm &times; {len_mm:.2f} mm<br/>"
                f"<b>Nominal Pin:</b> &Oslash; {pin_mm:.2f} mm (Finger thickness: {wall_mm:.2f} mm)"
            )
        else:
            h_mm = params['height'] * 10.0
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

            args.areInputsValid = True
        except:
            args.areInputsValid = False


def _commit_feature(ui_builder: SchemaUIBuilder) -> bool:
    """Generates feature geometry (Compliant Fins, Coathanger, or Crush Ribs) into the model."""
    if not ui_builder:
        return False

    hole_selection = ui_builder.get_item('holeSelection')
    if not hole_selection or hole_selection.selectionCount == 0:
        return False

    entity = hole_selection.selection(0).entity
    face = adsk.fusion.BRepFace.cast(entity)
    if not face or not face.isValid:
        return False

    target_body = face.body
    parent_component = target_body.parentComponent
    params = ui_builder.get_feature_parameters()
    mode = params.get('type')

    flip = bool(params.get('flip_direction', False))
    cyl_data = CylinderData(face, flip)

    if mode == TYPE_COMPLIANT_FINS:
        tool_body = create_compliant_fins_body(cyl_data, params)
        if not tool_body or not tool_body.isValid:
            return False
        apply_tool_body(parent_component, target_body, tool_body, "Compliant Fins", cyl_data, params)
        return True

    elif mode == TYPE_COATHANGER:
        cutter_body = create_coathanger_cutter(cyl_data, params)
        if not cutter_body or not cutter_body.isValid:
            return False
        apply_cutter_body(parent_component, target_body, cutter_body, "Coathanger Flexure")
        return True

    elif mode == TYPE_CRUSH_RIBS:
        tool_body = create_crush_ribs_body(cyl_data, params)
        if not tool_body or not tool_body.isValid:
            return False
        apply_tool_body(parent_component, target_body, tool_body, "Crush Ribs", cyl_data, params)
        return True

    return False


class ExecutePreviewHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _active_ui_builder
        try:
            preview_box = _active_ui_builder.get_item('showPreview') if _active_ui_builder else None
            if preview_box and not preview_box.value:
                return

            if _commit_feature(_active_ui_builder):
                args.isValidResult = True
            else:
                args.isValidResult = False
        except:
            args.isValidResult = False


class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _active_ui_builder
        try:
            preview_box = _active_ui_builder.get_item('showPreview') if _active_ui_builder else None
            if not preview_box or not preview_box.value:
                if not _commit_feature(_active_ui_builder):
                    args.executeFailed = True
                    args.executeFailedMessage = "Could not generate feature. Please verify parameters."
        except Exception as e:
            args.executeFailed = True
            args.executeFailedMessage = f"Operation failed:\n{traceback.format_exc()}"


class DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args: adsk.core.CommandEventArgs):
        global _active_ui_builder
        _active_ui_builder = None


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
    global _app, _ui, _handlers, _active_ui_builder
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
        _active_ui_builder = None
    except:
        pass
