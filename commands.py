"""
Command lifecycle, event handlers, and toolbar integration for Wiggly Hole Generator.
"""

import os
import traceback
import adsk.core
import adsk.fusion

from .config import (
    COMMAND_ID, LEGACY_COMMAND_ID, COMMAND_NAME, COMMAND_DESCRIPTION,
    PANEL_ID, FeatureType
)
try:
    from .ui import WigglyHoleGeneratorUI
except ImportError:
    from .ui import CrushFinsUI as WigglyHoleGeneratorUI
from .geometry import create_crush_ribs_body, create_compliant_fins_body, apply_tool_body

# Retain running command instances to prevent GC
running_commands = set()


def _make_forwarding_handler(handler_cls, callback):
    """Factory creating a forwarder handler that wraps callback with error handling."""
    class ForwardingHandler(handler_cls):
        def __init__(self, cb):
            super().__init__()
            self.cb = cb

        def notify(self, args):
            try:
                self.cb(args)
            except:
                app = adsk.core.Application.get()
                if app and app.userInterface:
                    app.userInterface.messageBox(f"Error in {callback.__name__}:\n{traceback.format_exc()}")
    return ForwardingHandler(callback)


class WigglyHoleGeneratorCommand:
    """Manages an active instance of the Wiggly Hole Generator command."""

    def __init__(self, cmd: adsk.core.Command):
        self.cmd = cmd
        running_commands.add(self)

        # Wire up event handlers
        self._input_changed_handler = _make_forwarding_handler(
            adsk.core.InputChangedEventHandler, self.on_input_changed
        )
        self.cmd.inputChanged.add(self._input_changed_handler)

        self._validate_handler = _make_forwarding_handler(
            adsk.core.ValidateInputsEventHandler, self.on_validate
        )
        self.cmd.validateInputs.add(self._validate_handler)

        self._execute_preview_handler = _make_forwarding_handler(
            adsk.core.CommandEventHandler, self.on_execute_preview
        )
        self.cmd.executePreview.add(self._execute_preview_handler)

        self._execute_handler = _make_forwarding_handler(
            adsk.core.CommandEventHandler, self.on_execute
        )
        self.cmd.execute.add(self._execute_handler)

        self._destroy_handler = _make_forwarding_handler(
            adsk.core.CommandEventHandler, self.on_destroy
        )
        self.cmd.destroy.add(self._destroy_handler)

        # Initialize dialog UI
        self.ui = WigglyHoleGeneratorUI(cmd.commandInputs)

    def on_input_changed(self, args: adsk.core.InputChangedEventArgs):
        self.ui.on_input_changed(args.input)

    def on_validate(self, args: adsk.core.ValidateInputsEventArgs):
        args.areInputsValid = self.ui.are_inputs_valid()

    def on_execute_preview(self, args: adsk.core.CommandEventArgs):
        if not self.ui.is_preview_enabled():
            return
        
        try:
            if self.do_execute():
                args.isValidResult = True
                self.ui.clear_status()
            else:
                args.isValidResult = False
        except Exception as e:
            args.isValidResult = False
            self.ui.set_status(f"Preview calculation failed: {str(e)}", is_error=True)

    def on_execute(self, args: adsk.core.CommandEventArgs):
        try:
            if not self.do_execute():
                args.executeFailed = True
                args.executeFailedMessage = "Could not generate ribs. Please check input parameters and hole geometry."
        except Exception as e:
            args.executeFailed = True
            args.executeFailedMessage = f"Operation failed:\n{traceback.format_exc()}"

    def do_execute(self) -> bool:
        """Executes the solid generation and joins into the target body."""
        if not self.ui.are_inputs_valid():
            return False

        params = self.ui.get_feature_parameters()
        cyl_data = params['cyl_data']
        feature_type = params['type']

        # Generate 3D tool body using TemporaryBRepManager
        if feature_type == FeatureType.CRUSH_RIBS:
            tool_body = create_crush_ribs_body(cyl_data, params)
        else:
            tool_body = create_compliant_fins_body(cyl_data, params)

        if not tool_body or not tool_body.isValid:
            self.ui.set_status("Failed to generate solid tool body.", is_error=True)
            return False

        # Apply to model via Combine Join (with optional root fillets)
        try:
            apply_tool_body(
                params['component'],
                params['target_body'],
                tool_body,
                feature_name=feature_type,
                cyl_data=cyl_data,
                params=params
            )
            return True
        except Exception as e:
            self.ui.set_status(f"Combine Join failed: {str(e)}", is_error=True)
            return False

    def on_destroy(self, args: adsk.core.CommandEventArgs):
        if self in running_commands:
            running_commands.remove(self)


CrushFinsCommand = WigglyHoleGeneratorCommand


class CommandManager:
    """Handles adding and removing the command definition and toolbar button."""

    def __init__(self, resource_dir: str):
        self.resource_dir = resource_dir
        self.app = adsk.core.Application.get()
        self.ui = self.app.userInterface
        self._created_handler = None

    def add_to_ui(self):
        self.remove_from_ui()

        # Create command definition
        cmd_defs = self.ui.commandDefinitions
        cmd_def = cmd_defs.itemById(COMMAND_ID)
        if not cmd_def:
            cmd_def = cmd_defs.addButtonDefinition(
                COMMAND_ID,
                COMMAND_NAME,
                COMMAND_DESCRIPTION,
                self.resource_dir
            )
            cmd_def.toolClipFilename = os.path.join(self.resource_dir, '32x32@2x.png')

        self._created_handler = _make_forwarding_handler(
            adsk.core.CommandCreatedEventHandler, self._on_command_created
        )
        cmd_def.commandCreated.add(self._created_handler)

        # Add button to Solid Create panel
        panel = self.ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            control = panel.controls.itemById(COMMAND_ID)
            if not control:
                control = panel.controls.addCommand(cmd_def)
                control.isPromoted = True
                control.isPromotedByDefault = True

    def _on_command_created(self, args: adsk.core.CommandCreatedEventArgs):
        cmd = args.command
        cmd.isRepeatable = True
        WigglyHoleGeneratorCommand(cmd)

    def remove_from_ui(self):
        # Remove control from panel
        panel = self.ui.allToolbarPanels.itemById(PANEL_ID)
        for cid in (COMMAND_ID, LEGACY_COMMAND_ID):
            if panel:
                ctrl = panel.controls.itemById(cid)
                if ctrl:
                    ctrl.deleteMe()

            # Remove command definition
            cmd_def = self.ui.commandDefinitions.itemById(cid)
            if cmd_def:
                cmd_def.deleteMe()
