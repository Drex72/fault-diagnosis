"""The tool contract. The simulator and the ESP32 firmware both implement it.

Schemas are JSON Schema (the subset validated below), so the same definitions
are sent to the LLM as OpenAI-style tool specs.
"""
from __future__ import annotations

from .measure import CURRENTS, PROBE_POINTS, TAPS

# Circuit elements a diagnosis can name. This is the bench topology (what is
# on the schematic), not a list of faults.
ELEMENTS = (
    "battery", "main_wiring", "motor_connection", "motor", "lamp_connection", "lamp",
    "load_connection", "load", "ground_return", "unmonitored_load",
    "sensor_src", "sensor_bus", "sensor_motor", "sensor_lamp", "sensor_load",
    "current_sensor_src", "current_sensor_motor",
)


def _obj(props: dict, required=None) -> dict:
    return {"type": "object", "properties": props,
            "required": list(required if required is not None else props), "additionalProperties": False}


TOOL_SCHEMAS = {
    "read_voltage": {
        "description": "Read one multiplexed voltage tap (volts, relative to battery negative).",
        "parameters": _obj({"point": {"type": "string", "enum": list(TAPS)}}),
    },
    "read_current": {
        "description": "Read a current sensor (amps). Only the battery output and motor branch are monitored.",
        "parameters": _obj({"channel": {"type": "string", "enum": list(CURRENTS)}}),
    },
    "load_test": {
        "description": ("Switch the 4 ohm test load across the bus. 'pulse' is short and shows the battery's "
                        "ohmic drop; 'steady' is longer and includes polarisation. Interrupts normal operation."),
        "parameters": _obj({
            "mode": {"type": "string", "enum": ["pulse", "steady"]},
            "width_ms": {"type": "integer", "minimum": 10, "maximum": 500},
        }, required=["mode"]),
    },
    "human_probe": {
        "description": ("Ask the technician to measure the voltage between two points with a handheld meter. "
                        "Slow and costly, but reaches points the bench cannot, such as 'ret' (the shared return node)."),
        "parameters": _obj({
            "point_a": {"type": "string", "enum": list(PROBE_POINTS)},
            "point_b": {"type": "string", "enum": list(PROBE_POINTS)},
            "instruction": {"type": "string", "minLength": 1},
        }, required=["point_a", "point_b"]),
    },
    "get_history": {
        "description": "Return all measurements taken so far this session. Free.",
        "parameters": _obj({}),
    },
}


def validate(schema: dict, value, path: str = "args") -> list[str]:
    """Validate value against a small JSON Schema subset. Returns error strings."""
    errs: list[str] = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errs.append(f"{path}.{name}: required")
        for name, v in value.items():
            if name not in props:
                if schema.get("additionalProperties") is False:
                    errs.append(f"{path}.{name}: unexpected field")
                continue
            errs += validate(props[name], v, f"{path}.{name}")
    elif t == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        if len(value) > schema.get("maxItems", len(value)):
            errs.append(f"{path}: too many items")
        for i, item in enumerate(value):
            errs += validate(schema.get("items", {}), item, f"{path}[{i}]")
    elif t == "string":
        if not isinstance(value, str):
            return [f"{path}: expected string"]
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: empty")
    elif t in ("integer", "number"):
        ok = isinstance(value, int) if t == "integer" else isinstance(value, (int, float))
        if isinstance(value, bool) or not ok:
            return [f"{path}: expected {t}"]
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above maximum {schema['maximum']}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not one of {schema['enum']}")
    return errs


def validate_call(name: str, args, schemas: dict = TOOL_SCHEMAS) -> list[str]:
    if name not in schemas:
        return [f"unknown tool {name!r}"]
    return validate(schemas[name]["parameters"], args)


def openai_tools(schemas: dict = TOOL_SCHEMAS) -> list[dict]:
    return [{"type": "function", "function": {"name": n, **s}} for n, s in schemas.items()]
