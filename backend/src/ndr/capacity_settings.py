"""MB configuration inputs; runtime and API limits remain integer bytes."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

BYTES_PER_MB = 1024 * 1024
CAPACITY_KEYS = {
    "max_import_bytes": "max_import_mb",
    "max_epub_total_uncompressed_bytes": "max_epub_total_uncompressed_mb",
    "max_epub_entry_bytes": "max_epub_entry_mb",
}


def mb_to_bytes(value, name: str) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > 51200:
            raise ValueError
        result = int((number * BYTES_PER_MB).to_integral_value(rounding=ROUND_HALF_UP))
        if result < 1:
            raise ValueError
        return result
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(f"{name}必须是有效的正数MB容量（最多51200 MB）。") from exc


def capacity_source(source):
    """Convert each source independently so environment > saved > dotenv stays intact."""

    def read():
        values = source()
        for byte_key, mb_key in CAPACITY_KEYS.items():
            variable = f"ndr_{mb_key}"
            raw = source.env_vars.get(variable)
            if raw is not None:
                values[byte_key] = mb_to_bytes(raw, variable.upper())
                # DotenvSettingsSource includes unrecognised fields as extras.
                values.pop(variable, None)
        return values

    return read
