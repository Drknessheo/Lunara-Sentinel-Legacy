import re


class SlipParseError(Exception):
    pass


def parse_slip(message: str) -> dict:
    # Normalize and clean input
    lines = [line.strip() for line in message.strip().splitlines() if line.strip()]
    slip_data = {}

    # Define expected keys and regex patterns
    expected_fields = {
        "SLIP": r"SLIP:\s*(\w+)",
        "ACTION": r"ACTION:\s*(BUY|SELL)",
        "AMOUNT": r"AMOUNT:\s*([\d.]+)",
        "PRICE": r"PRICE:\s*(MARKET|LIMIT)",
        "RISK": r"RISK:\s*([\d.]+)%",
    }

    for field, pattern in expected_fields.items():
        match = next(
            (
                re.match(pattern, line, re.IGNORECASE)
                for line in lines
                if line.upper().startswith(field)
            ),
            None,
        )
        if not match:
            raise SlipParseError(f"Missing or invalid field: {field}")
        slip_data[field.lower()] = match.group(1)

    # Convert types
    try:
        return {
            "symbol": slip_data["slip"].upper(),
            "action": slip_data["action"].upper(),
            "amount": float(slip_data["amount"]),
            "price_type": slip_data["price"].upper(),
            "risk_percent": float(slip_data["risk"]),
        }
    except ValueError as e:
        raise SlipParseError(f"Type conversion error: {e}")
