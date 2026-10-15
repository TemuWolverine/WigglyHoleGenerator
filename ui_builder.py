"""
Declarative UI Builder for Autodesk Fusion 360 Add-Ins.
Dynamically constructs native CommandInputs from a declarative JSON schema,
manages two-way data bindings, and automates conditional visibility rules.
"""

import os
import json
import math
import traceback
import adsk.core
import adsk.fusion


class SchemaUIBuilder:
    """Instantiates and manages native Fusion 360 CommandInputs from a declarative schema."""

    def __init__(self, inputs: adsk.core.CommandInputs, schema_path: str, resource_dir: str):
        self.inputs = inputs
        self.resource_dir = resource_dir
        self.schema_path = schema_path
        self.items = {}
        self.visibility_rules = {}  # {item_id: rule_dict}
        self.groups = {}

        # Determine default units
        app = adsk.core.Application.get()
        product = app.activeProduct
        units_mgr = product.unitsManager if product else None
        self.length_unit = units_mgr.defaultLengthUnits if units_mgr else 'mm'

        # Load schema definition
        with open(schema_path, 'r', encoding='utf-8') as f:
            self.schema = json.load(f)

        self._build_all()

    def _build_all(self):
        """Constructs all native inputs defined in the schema."""
        # 1. Selection Input
        sel_def = self.schema.get('selection')
        if sel_def:
            sel_id = sel_def['id']
            sel_input = self.inputs.addSelectionInput(
                sel_id,
                sel_def.get('label', ''),
                sel_def.get('prompt', '')
            )
            if 'filter' in sel_def:
                sel_input.addSelectionFilter(sel_def['filter'])
            limits = sel_def.get('limits', [1, 1])
            sel_input.setSelectionLimits(limits[0], limits[1])
            if 'tooltip' in sel_def:
                sel_input.tooltip = sel_def['tooltip']
            self.items[sel_id] = sel_input

        # 2. Header Inputs (e.g. Button Rows, Readout boxes)
        for input_def in self.schema.get('header_inputs', []):
            self._create_input(self.inputs, input_def)

        # 3. Parameter Groups & Children
        for group_def in self.schema.get('groups', []):
            group_id = group_def['id']
            group_input = self.inputs.addGroupCommandInput(group_id, group_def.get('label', ''))
            group_input.isExpanded = group_def.get('isExpanded', True)
            self.items[group_id] = group_input
            self.groups[group_id] = group_input

            if 'visible_when' in group_def:
                self.visibility_rules[group_id] = group_def['visible_when']

            children = group_input.children
            for child_def in group_def.get('inputs', []):
                self._create_input(children, child_def)

        # 4. Footer Inputs (e.g. Show Preview)
        for input_def in self.schema.get('footer_inputs', []):
            self._create_input(self.inputs, input_def)

        # Initial visibility evaluation
        self.update_visibility()

    def _create_input(self, container: adsk.core.CommandInputs, item_def: dict):
        """Creates a single native input based on its declared type."""
        inp_id = item_def['id']
        inp_type = item_def.get('type')
        label = item_def.get('label', '')

        new_input = None

        if inp_type == 'value':
            unit = item_def.get('unit', self.length_unit)
            default_val = item_def.get('default', '0 mm')
            val_input = adsk.core.ValueInput.createByString(default_val)
            new_input = container.addValueInput(inp_id, label, unit, val_input)

        elif inp_type == 'spinner':
            min_val = item_def.get('min', 0)
            max_val = item_def.get('max', 100)
            step = item_def.get('step', 1)
            default_val = item_def.get('default', min_val)
            new_input = container.addIntegerSpinnerCommandInput(
                inp_id, label, min_val, max_val, step, default_val
            )

        elif inp_type == 'bool':
            default_val = item_def.get('default', True)
            new_input = container.addBoolValueInput(inp_id, label, True, '', default_val)

        elif inp_type == 'dropdown':
            style = adsk.core.DropDownStyles.TextListDropDownStyle
            new_input = container.addDropDownCommandInput(inp_id, label, style)
            items = item_def.get('items', [])
            default_item = item_def.get('default', items[0] if items else '')
            for itm in items:
                new_input.listItems.add(itm, (itm == default_item))

        elif inp_type == 'button_row':
            is_multi = item_def.get('isMultiSelect', False)
            new_input = container.addButtonRowCommandInput(inp_id, label, is_multi)
            for itm in item_def.get('items', []):
                itm_name = itm['name']
                is_selected = itm.get('selected', False)
                icon_path = itm.get('icon', '')
                if icon_path:
                    schema_dir = os.path.dirname(os.path.abspath(self.schema_path))
                    icon_path = os.path.normpath(os.path.join(schema_dir, icon_path)).replace('\\', '/')
                new_input.listItems.add(itm_name, is_selected, icon_path)

        elif inp_type == 'text_box':
            default_text = item_def.get('default', '')
            line_count = item_def.get('lineCount', 1)
            is_read_only = item_def.get('isReadOnly', True)
            new_input = container.addTextBoxCommandInput(inp_id, label, default_text, line_count, is_read_only)
            if item_def.get('isFullWidth', False):
                new_input.isFullWidth = True

        if new_input:
            if 'tooltip' in item_def:
                new_input.tooltip = item_def['tooltip']
            self.items[inp_id] = new_input
            if 'visible_when' in item_def:
                self.visibility_rules[inp_id] = item_def['visible_when']

        return new_input

    def update_visibility(self):
        """Evaluates all declarative visible_when rules and updates widget visibility."""
        for item_id, rule in self.visibility_rules.items():
            field_name = rule.get('field')
            expected_val = rule.get('equals')
            current_val = self.get_value(field_name)
            is_match = (current_val == expected_val)

            item = self.items.get(item_id)
            if item:
                item.isVisible = is_match

    def on_input_changed(self, changed_input: adsk.core.CommandInput):
        """Called by command lifecycle when any input value changes."""
        self.update_visibility()

    def get_value(self, input_id: str):
        """Extracts the typed value of an input by its identifier."""
        item = self.items.get(input_id)
        if not item:
            return None

        # ValueCommandInput returns internal float (cm or rad)
        if isinstance(item, adsk.core.ValueCommandInput):
            return item.value
        # BoolValueCommandInput returns bool
        elif isinstance(item, adsk.core.BoolValueCommandInput):
            return item.value
        # IntegerSpinnerCommandInput returns int
        elif isinstance(item, adsk.core.IntegerSpinnerCommandInput):
            return item.value
        # DropDownCommandInput
        elif isinstance(item, adsk.core.DropDownCommandInput):
            return item.selectedItem.name if item.selectedItem else ''
        # ButtonRowCommandInput
        elif isinstance(item, adsk.core.ButtonRowCommandInput):
            return item.selectedItem.name if item.selectedItem else ''
        # TextBoxCommandInput
        elif isinstance(item, adsk.core.TextBoxCommandInput):
            return item.text
        # SelectionCommandInput
        elif isinstance(item, adsk.core.SelectionCommandInput):
            return item.selection(0).entity if item.selectionCount > 0 else None

        return None

    def get_item(self, input_id: str):
        """Returns the native CommandInput object."""
        return self.items.get(input_id)

    def set_formatted_text(self, input_id: str, html_text: str):
        """Updates formatted HTML text for a TextBox input."""
        item = self.items.get(input_id)
        if item and isinstance(item, adsk.core.TextBoxCommandInput):
            item.formattedText = html_text

    def get_selected_feature_type(self) -> str:
        """Returns the active mode (e.g. 'Compliant Fins', 'Coathanger', 'Crush Ribs')."""
        return str(self.get_value('featureType') or 'Compliant Fins')

    def get_feature_parameters(self) -> dict:
        """Extracts all active feature parameters converted to internal units."""
        mode = self.get_selected_feature_type()

        if mode == 'Compliant Fins':
            full_depth = bool(self.get_value('finFullDepth'))
            return {
                'type': mode,
                'count': int(self.get_value('finCount') or 4),
                'thickness': float(self.get_value('finThickness') or 0.08),
                'thickness_root': float(self.get_value('finRootThickness') or 0.12),
                'protrusion': float(self.get_value('finProtrusion') or 0.1),
                'slant_angle_deg': math.degrees(float(self.get_value('finSlant') or math.radians(45.0))),
                'direction': str(self.get_value('finDirection') or 'Clockwise'),
                'full_depth': full_depth,
                'length': float(self.get_value('finLength') or 1.0),
                'offset': float(self.get_value('finOffset') or 0.0),
                'has_lead_in': bool(self.get_value('finLeadIn')),
                'lead_in_length': float(self.get_value('finLeadInLength') or 0.15),
                'has_fillet': bool(self.get_value('finFillet')),
                'fillet_radius': float(self.get_value('finFilletRadius') or 0.05),
                'flip_direction': bool(self.get_value('finFlip'))
            }

        elif mode == 'Coathanger':
            return {
                'type': mode,
                'wall_thickness': float(self.get_value('coatWallThickness') or 0.12),
                'slot_width': float(self.get_value('coatSlotWidth') or 0.15),
                'arc_deg': math.degrees(float(self.get_value('coatArcSpan') or math.radians(120.0))),
                'slit_width': float(self.get_value('coatSlitWidth') or 0.1),
                'rotation_deg': math.degrees(float(self.get_value('coatRotationAngle') or 0.0))
            }

        elif mode == 'Crush Ribs':
            full_depth = bool(self.get_value('crushFullDepth'))
            return {
                'type': mode,
                'count': int(self.get_value('crushCount') or 4),
                'width': float(self.get_value('crushWidth') or 0.08),
                'height': float(self.get_value('crushHeight') or 0.03),
                'full_depth': full_depth,
                'length': float(self.get_value('crushLength') or 1.0),
                'has_lead_in': bool(self.get_value('crushLeadIn')),
                'lead_in_length': float(self.get_value('crushLeadInLength') or 0.15),
                'flip_direction': bool(self.get_value('crushFlip'))
            }

        return {}
