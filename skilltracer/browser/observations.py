"""Accessibility-tree field extraction for benchmark login forms."""

import re


def extract_all_elements(ax_tree: str) -> list[dict]:
    """
    Extract all the elements from the accessibility tree
    sample element object structure:
    {
        "id": "1",
        "role": "button",
        "value": "Click me",
        "attributes": [...]
    }
    Args:
        ax_tree: The accessibility tree of the web page
    Returns:
        A list of elements
    """
    elements = []
    lines = ax_tree.strip().split("\n")

    for line in lines:
        # Skip empty lines
        if not line.strip():
            continue

        # Remove leading tabs (indentation) - we don't need it for element extraction
        line = line.lstrip("\t")

        # Pattern to match: [ID] role 'value', attributes
        # Or: role 'value', attributes
        # Or: [ID] role '', attributes

        # First, try to extract ID if present
        element_id = None
        if line.startswith("["):
            id_match = re.match(r"\[(\d+)\]\s*(.+)", line)
            if id_match:
                element_id = id_match.group(1)
                line = id_match.group(2)

        # Now parse role, value, and attributes
        # Pattern: role 'value', attr1, attr2=value2
        # Or: role '', attr1, attr2=value2

        # Extract role (first word)
        role_match = re.match(r"^(\w+)(?:\s+(.+))?$", line)
        if not role_match:
            continue

        role = role_match.group(1)
        rest = role_match.group(2).strip() if role_match.group(2) else ""

        # Extract value (text in quotes)
        value = ""
        attributes = []

        # Check if there's a quoted value
        value_match = re.match(r"^'([^']*)'", rest)
        if value_match:
            value = value_match.group(1)
            rest = rest[len(value_match.group(0)) :].strip()
        else:
            # Check for empty quotes
            if rest.startswith("''"):
                value = ""
                rest = rest[2:].strip()

        # Parse attributes (comma-separated)
        if rest:
            # Remove leading comma if present
            if rest.startswith(","):
                rest = rest[1:].strip()

            # Split by comma, but be careful with quoted values in attributes
            attr_parts = []
            current_part = ""
            in_quotes = False
            quote_char = None

            for char in rest:
                if char in ("'", '"') and not in_quotes:
                    in_quotes = True
                    quote_char = char
                    current_part += char
                elif char == quote_char and in_quotes:
                    in_quotes = False
                    quote_char = None
                    current_part += char
                elif char == "," and not in_quotes:
                    if current_part.strip():
                        attr_parts.append(current_part.strip())
                    current_part = ""
                else:
                    current_part += char

            if current_part.strip():
                attr_parts.append(current_part.strip())

            # Parse each attribute
            for attr_str in attr_parts:
                attr_str = attr_str.strip()
                if not attr_str:
                    continue

                # Check if it's a key=value attribute
                if "=" in attr_str:
                    key, val = attr_str.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Remove quotes from value if present
                    if (val.startswith("'") and val.endswith("'")) or (
                        val.startswith('"') and val.endswith('"')
                    ):
                        val = val[1:-1]
                    attributes.append(f"{key}='{val}'")
                else:
                    # It's a boolean attribute (like "focused")
                    attributes.append(attr_str)

        elements.append({"id": element_id, "role": role, "value": value, "attributes": attributes})

    return elements
