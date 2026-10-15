"""
User Interface management and CommandInputs setup for Wiggly Hole Generator.
"""

import math
import traceback
import adsk.core
import adsk.fusion

from .config import (
    FeatureType, RibProfile, FinDirection,
    calculate_crush_rib_defaults, calculate_compliant_fin_defaults
)
from .geometry import CylinderData


class WigglyHoleGeneratorUI:
    """Manages dialog inputs, event callbacks, visibility updates, and parameter parsing."""

    def __init__(self, inputs: adsk.core.CommandInputs):
        self.inputs = inputs
        app = adsk.core.Application.get()
        units_mgr = app.activeProduct.unitsManager
        self.length_unit = units_mgr.defaultLengthUnits

        self._has_customized_crush = False
        self._has_customized_fin = False

        current_dir = os.path.dirname(os.path.realpath(__file__))
        self.resource_dir = os.path.join(current_dir, 'resources')
        self._is_updating = False

        # --- 1. Selection Input ---
        self.hole_selection = inputs.addSelectionInput(
            'holeSelection', 'Hole Face', 'Select cylindrical face of the hole'
        )
        self.hole_selection.addSelectionFilter('CylindricalFaces')
        self.hole_selection.setSelectionLimits(1, 1)
        self.hole_selection.tooltip = 'Select the cylindrical interior face of the hole.'

        # --- 2. Feature Type Selector ---
        self.feature_type_row = inputs.addButtonRowCommandInput('featureType', 'Type', False)
        self.feature_type_row.listItems.add(
            FeatureType.CRUSH_RIBS, True, os.path.join(self.resource_dir, 'ui', 'crush_ribs')
        )
        self.feature_type_row.listItems.add(
            FeatureType.COMPLIANT_FINS, False, os.path.join(self.resource_dir, 'ui', 'compliant_fins')
        )
        self.feature_type_row.tooltip = 'Select between Crush Ribs (deform plastically) or Compliant Fins (flex elastically).'

        # --- 3. Dynamic Hole / Pin Info Display ---
        self.info_box = inputs.addTextBoxCommandInput('infoBox', '', 'Please select a cylindrical hole face.', 2, True)
        self.info_box.isFullWidth = True

        # =========================================================================
        # --- 4. Crush Ribs Group ---
        # =========================================================================
        self.crush_group = inputs.addGroupCommandInput('crushGroup', 'Crush Ribs Parameters')
        self.crush_group.isExpanded = True
        c_inputs = self.crush_group.children

        self.crush_profile = c_inputs.addButtonRowCommandInput('crushProfile', 'Profile', False)
        self.crush_profile.listItems.add(
            RibProfile.TRIANGULAR, True, os.path.join(self.resource_dir, 'ui', 'profile_triangular')
        )
        self.crush_profile.listItems.add(
            RibProfile.ROUNDED, False, os.path.join(self.resource_dir, 'ui', 'profile_rounded')
        )
        self.crush_profile.listItems.add(
            RibProfile.TRAPEZOIDAL, False, os.path.join(self.resource_dir, 'ui', 'profile_trapezoidal')
        )

        self.crush_count = c_inputs.addIntegerSpinnerCommandInput('crushCount', 'Rib Count', 2, 24, 1, 4)
        self.crush_count.tooltip = 'Number of ribs evenly spaced around the hole circumference.'

        self.crush_height = c_inputs.addValueInput(
            'crushHeight', 'Rib Height', self.length_unit, adsk.core.ValueInput.createByString('0.3 mm')
        )
        self.crush_height.tooltip = 'Radial interference: how far the rib crest extends inward into the hole.'

        self.crush_width = c_inputs.addValueInput(
            'crushWidth', 'Rib Width', self.length_unit, adsk.core.ValueInput.createByString('0.8 mm')
        )
        self.crush_width.tooltip = 'Width of the rib base.'

        self.crush_apex_angle = c_inputs.addValueInput(
            'crushApexAngle', 'Apex Angle', 'deg', adsk.core.ValueInput.createByString('60 deg')
        )
        self.crush_apex_angle.tooltip = 'Apex angle of the pointed triangular rib.'

        self.crush_full_depth = c_inputs.addBoolValueInput('crushFullDepth', 'Full Depth', True, '', True)
        self.crush_full_depth.tooltip = 'Extends the ribs along the entire depth of the hole.'

        self.crush_length = c_inputs.addValueInput(
            'crushLength', 'Length', self.length_unit, adsk.core.ValueInput.createByString('10 mm')
        )
        self.crush_offset = c_inputs.addValueInput(
            'crushOffset', 'Offset', self.length_unit, adsk.core.ValueInput.createByString('0 mm')
        )

        self.crush_lead_in = c_inputs.addBoolValueInput('crushLeadIn', 'Lead-in Taper', True, '', True)
        self.crush_lead_in.tooltip = 'Adds a tapered lead-in chamfer at the entrance for smooth pin entry.'

        self.crush_lead_in_length = c_inputs.addValueInput(
            'crushLeadInLength', 'Lead-in Length', self.length_unit, adsk.core.ValueInput.createByString('1.5 mm')
        )

        self.crush_flip = c_inputs.addBoolValueInput('crushFlip', 'Flip Direction', True, '', False)
        self.crush_flip.tooltip = 'Reverses the hole entrance / start direction.'

        # =========================================================================
        # --- 5. Compliant Fins Group ---
        # =========================================================================
        self.fin_group = inputs.addGroupCommandInput('finGroup', 'Compliant Fins Parameters')
        self.fin_group.isExpanded = True
        f_inputs = self.fin_group.children

        self.fin_count = f_inputs.addIntegerSpinnerCommandInput('finCount', 'Fin Count', 2, 16, 1, 4)
        self.fin_count.tooltip = 'Number of flexural fins spaced circumferentially around the hole.'

        self.fin_thickness = f_inputs.addValueInput(
            'finThickness', 'Fin Thickness', self.length_unit, adsk.core.ValueInput.createByString('0.8 mm')
        )
        self.fin_thickness.tooltip = 'Flexural thickness of each fin.'

        self.fin_slant_angle = f_inputs.addValueInput(
            'finSlantAngle', 'Slant Angle', 'deg', adsk.core.ValueInput.createByString('45 deg')
        )
        self.fin_slant_angle.tooltip = 'Angle of the fin relative to the radial line.'

        self.fin_protrusion = f_inputs.addValueInput(
            'finProtrusion', 'Protrusion Height', self.length_unit, adsk.core.ValueInput.createByString('0.5 mm')
        )
        self.fin_protrusion.tooltip = 'Radial reach of the fin inward into the hole.'

        self.fin_direction = f_inputs.addDropDownCommandInput(
            'finDirection', 'Slant Direction', adsk.core.DropDownStyles.TextListDropDownStyle
        )
        self.fin_direction.listItems.add(FinDirection.CLOCKWISE, True)
        self.fin_direction.listItems.add(FinDirection.COUNTER_CLOCKWISE, False)

        self.fin_full_depth = f_inputs.addBoolValueInput('finFullDepth', 'Full Depth', True, '', True)
        self.fin_length = f_inputs.addValueInput(
            'finLength', 'Length', self.length_unit, adsk.core.ValueInput.createByString('10 mm')
        )
        self.fin_offset = f_inputs.addValueInput(
            'finOffset', 'Offset', self.length_unit, adsk.core.ValueInput.createByString('0 mm')
        )

        self.fin_lead_in = f_inputs.addBoolValueInput('finLeadIn', 'Lead-in Chamfer', True, '', True)
        self.fin_lead_in_length = f_inputs.addValueInput(
            'finLeadInLength', 'Lead-in Length', self.length_unit, adsk.core.ValueInput.createByString('1.5 mm')
        )

        self.fin_flip = f_inputs.addBoolValueInput('finFlip', 'Flip Direction', True, '', False)
        self.fin_flip.tooltip = 'Reverses the hole entrance / start direction.'

        self.fin_fillet = f_inputs.addBoolValueInput('finFillet', 'Root Fillet', True, '', True)
        self.fin_fillet.tooltip = 'Add a fillet where the fins connect to the hole wall to relieve stress concentration.'
        self.fin_fillet_radius = f_inputs.addValueInput(
            'finFilletRadius', 'Fillet Radius', self.length_unit, adsk.core.ValueInput.createByString('0.5 mm')
        )
        self.fin_fillet_radius.tooltip = 'Radius of the fillet blending the fin root into the hole wall.'

        # --- 6. Preview and Status ---
        self.preview_checkbox = inputs.addBoolValueInput('showPreview', 'Show Preview', True, '', True)
        self.status_message = inputs.addTextBoxCommandInput('statusMessage', '', '', 2, True)
        self.status_message.isFullWidth = True
        self.status_message.isVisible = False

        self.update_visibility()

    def update_visibility(self):
        """Toggles visibility of dependent controls based on user selections."""
        is_crush = (self.feature_type_row.selectedItem.name == FeatureType.CRUSH_RIBS)
        self.crush_group.isVisible = is_crush
        self.fin_group.isVisible = not is_crush

        if is_crush:
            # Crush Ribs visibility
            profile_name = self.crush_profile.selectedItem.name
            self.crush_apex_angle.isVisible = (profile_name == RibProfile.TRIANGULAR)
            self.crush_width.isVisible = (profile_name != RibProfile.TRIANGULAR)
            self.crush_length.isVisible = not self.crush_full_depth.value
            self.crush_offset.isVisible = not self.crush_full_depth.value
            self.crush_lead_in_length.isVisible = self.crush_lead_in.value
        else:
            # Compliant Fins visibility
            self.fin_length.isVisible = not self.fin_full_depth.value
            self.fin_offset.isVisible = not self.fin_full_depth.value
            self.fin_lead_in_length.isVisible = self.fin_lead_in.value
            self.fin_fillet_radius.isVisible = self.fin_fillet.value

    def on_input_changed(self, changed_input: adsk.core.CommandInput):
        """Handles changes to any input field."""
        if self._is_updating:
            return

        if changed_input.id == self.hole_selection.id:
            self._handle_hole_selection()
        elif changed_input.id in (self.crush_flip.id, self.fin_flip.id):
            self._handle_flip()
        elif changed_input.id in (self.crush_height.id, self.fin_protrusion.id, self.feature_type_row.id):
            if changed_input.id == self.crush_height.id:
                self._has_customized_crush = True
            elif changed_input.id == self.fin_protrusion.id:
                self._has_customized_fin = True
            self._update_pin_info()
        elif changed_input.id in (self.crush_count.id, self.crush_width.id, self.crush_lead_in_length.id):
            self._has_customized_crush = True
        elif changed_input.id in (self.fin_count.id, self.fin_thickness.id, self.fin_lead_in_length.id, self.fin_fillet_radius.id):
            self._has_customized_fin = True
        elif changed_input.id == self.fin_fillet.id:
            self.fin_fillet_radius.isVisible = self.fin_fillet.value

        self.update_visibility()

    def _handle_hole_selection(self):
        """Adapts defaults when a hole is selected."""
        if self.hole_selection.selectionCount == 0:
            self.info_box.formattedText = 'Please select a cylindrical hole face.'
            return

        try:
            face = self.hole_selection.selection(0).entity
            flip = self.crush_flip.value if self.is_crush_selected() else self.fin_flip.value
            cyl_data = CylinderData(face, flip)

            dia_mm = cyl_data.diameter * 10.0
            len_mm = cyl_data.length * 10.0

            # Pre-populate suggested defaults if not customized yet
            crush_defs = calculate_crush_rib_defaults(dia_mm, len_mm)
            fin_defs = calculate_compliant_fin_defaults(dia_mm, len_mm)

            self._is_updating = True
            try:
                if not self._has_customized_crush:
                    self.crush_count.value = crush_defs['count']
                    self.crush_height.value = crush_defs['height_mm'] / 10.0
                    self.crush_width.value = crush_defs['width_mm'] / 10.0
                    self.crush_lead_in_length.value = crush_defs['lead_in_length_mm'] / 10.0
                    self.crush_length.value = cyl_data.length

                if not self._has_customized_fin:
                    self.fin_count.value = fin_defs['count']
                    self.fin_thickness.value = fin_defs['thickness_mm'] / 10.0
                    self.fin_protrusion.value = fin_defs['protrusion_mm'] / 10.0
                    self.fin_lead_in_length.value = fin_defs['lead_in_length_mm'] / 10.0
                    self.fin_fillet.value = fin_defs.get('has_fillet', True)
                    self.fin_fillet_radius.value = fin_defs.get('fillet_radius_mm', 0.5) / 10.0
                    self.fin_length.value = cyl_data.length
            finally:
                self._is_updating = False

            self._update_pin_info(cyl_data)

            if not cyl_data.is_internal:
                self.set_status('Warning: The selected face appears to be an external cylinder (boss). Ribs will be placed inward.', is_error=False)
            else:
                self.clear_status()

        except Exception as e:
            self.set_status(f"Error inspecting cylinder: {str(e)}", is_error=True)

    def _handle_flip(self):
        """Re-evaluates cylinder coordinate system when direction is flipped."""
        if self.hole_selection.selectionCount > 0:
            face = self.hole_selection.selection(0).entity
            flip = self.crush_flip.value if self.is_crush_selected() else self.fin_flip.value
            try:
                cyl_data = CylinderData(face, flip)
                self._update_pin_info(cyl_data)
            except:
                pass

    def _update_pin_info(self, cyl_data: CylinderData = None):
        """Updates the informational display of hole size and nominal mating pin diameter."""
        if self.hole_selection.selectionCount == 0:
            return

        try:
            if not cyl_data:
                face = self.hole_selection.selection(0).entity
                flip = self.crush_flip.value if self.is_crush_selected() else self.fin_flip.value
                cyl_data = CylinderData(face, flip)

            dia_mm = cyl_data.diameter * 10.0
            len_mm = cyl_data.length * 10.0

            if self.is_crush_selected():
                h_mm = self.crush_height.value * 10.0
                nom_pin = max(0.0, dia_mm - 2.0 * h_mm)
                self.info_box.formattedText = (
                    f"<b>Hole:</b> &Oslash; {dia_mm:.2f} mm &times; {len_mm:.2f} mm<br/>"
                    f"<b>Nominal Pin:</b> &Oslash; {nom_pin:.2f} mm (Crush interference: {h_mm:.2f} mm / side)"
                )
            else:
                prot_mm = self.fin_protrusion.value * 10.0
                inscribed_pin = max(0.0, dia_mm - 2.0 * prot_mm)
                self.info_box.formattedText = (
                    f"<b>Hole:</b> &Oslash; {dia_mm:.2f} mm &times; {len_mm:.2f} mm<br/>"
                    f"<b>Inscribed Pin:</b> &Oslash; {inscribed_pin:.2f} mm (Fin reach: {prot_mm:.2f} mm / side)"
                )
        except:
            pass

    def is_crush_selected(self) -> bool:
        return self.feature_type_row.selectedItem.name == FeatureType.CRUSH_RIBS

    def is_preview_enabled(self) -> bool:
        return self.preview_checkbox.value

    def are_inputs_valid(self) -> bool:
        """Validates all inputs before preview or execution."""
        if self.hole_selection.selectionCount == 0:
            self.set_status('Please select a cylindrical hole face.', is_error=True)
            return False

        try:
            face = self.hole_selection.selection(0).entity
            flip = self.crush_flip.value if self.is_crush_selected() else self.fin_flip.value
            cyl_data = CylinderData(face, flip)
        except Exception as e:
            self.set_status(f"Invalid hole selection: {str(e)}", is_error=True)
            return False

        if self.is_crush_selected():
            if self.crush_height.value <= 0.001 or self.crush_height.value >= cyl_data.radius:
                self.set_status(f"Rib height must be between 0 and hole radius ({cyl_data.radius*10.0:.2f} mm).", is_error=True)
                return False
            if self.crush_profile.selectedItem.name != RibProfile.TRIANGULAR and self.crush_width.value <= 0.001:
                self.set_status("Rib width must be greater than zero.", is_error=True)
                return False
            if not self.crush_full_depth.value and self.crush_length.value <= 0.001:
                self.set_status("Rib length must be greater than zero.", is_error=True)
                return False
        else:
            if self.fin_protrusion.value <= 0.001 or self.fin_protrusion.value >= cyl_data.radius:
                self.set_status(f"Fin protrusion must be between 0 and hole radius ({cyl_data.radius*10.0:.2f} mm).", is_error=True)
                return False
            if self.fin_thickness.value <= 0.001:
                self.set_status("Fin thickness must be greater than zero.", is_error=True)
                return False
            if not self.fin_full_depth.value and self.fin_length.value <= 0.001:
                self.set_status("Fin length must be greater than zero.", is_error=True)
                return False

        self.clear_status()
        return True

    def get_feature_parameters(self) -> dict:
        """Extracts and returns parsed parameters in internal units (cm, radians)."""
        is_crush = self.is_crush_selected()
        face = self.hole_selection.selection(0).entity
        flip = self.crush_flip.value if is_crush else self.fin_flip.value
        cyl_data = CylinderData(face, flip)

        if is_crush:
            return {
                'type': FeatureType.CRUSH_RIBS,
                'face': face,
                'target_body': face.body,
                'component': face.body.parentComponent,
                'cyl_data': cyl_data,
                'profile': self.crush_profile.selectedItem.name,
                'count': self.crush_count.value,
                'height': self.crush_height.value,
                'width': self.crush_width.value,
                'apex_angle_deg': math.degrees(self.crush_apex_angle.value),
                'full_depth': self.crush_full_depth.value,
                'length': self.crush_length.value,
                'offset': self.crush_offset.value,
                'has_lead_in': self.crush_lead_in.value,
                'lead_in_length': self.crush_lead_in_length.value,
                'flip_direction': self.crush_flip.value
            }
        else:
            return {
                'type': FeatureType.COMPLIANT_FINS,
                'face': face,
                'target_body': face.body,
                'component': face.body.parentComponent,
                'cyl_data': cyl_data,
                'count': self.fin_count.value,
                'thickness': self.fin_thickness.value,
                'slant_angle_deg': math.degrees(self.fin_slant_angle.value),
                'protrusion': self.fin_protrusion.value,
                'direction': self.fin_direction.selectedItem.name,
                'full_depth': self.fin_full_depth.value,
                'length': self.fin_length.value,
                'offset': self.fin_offset.value,
                'has_lead_in': self.fin_lead_in.value,
                'lead_in_length': self.fin_lead_in_length.value,
                'has_fillet': self.fin_fillet.value,
                'fillet_radius': self.fin_fillet_radius.value,
                'flip_direction': self.fin_flip.value
            }

    def set_status(self, message: str, is_error: bool = False):
        prefix = "<span style='color:red;'><b>Error:</b> " if is_error else "<span style='color:orange;'><b>Warning:</b> "
        self.status_message.formattedText = f"{prefix}{message}</span>"
        self.status_message.isVisible = bool(message)

    def clear_status(self):
        self.status_message.formattedText = ''
        self.status_message.isVisible = False


CrushFinsUI = WigglyHoleGeneratorUI
