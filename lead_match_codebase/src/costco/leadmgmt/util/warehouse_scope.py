def parse_warehouse_scope(warehouse):
    if warehouse is None:
        return []

    text = str(warehouse).strip()
    if not text or text.lower() in {"all", "none", "null"}:
        return []

    text = text.strip("[]")
    values = []
    for raw_value in text.split(","):
        value = raw_value.strip()
        if not value:
            continue
        if not value.isdigit():
            raise ValueError(f"Invalid WAREHOUSE value: {warehouse!r}")
        values.append(int(value))

    return values


def apply_warehouse_filter(query, warehouse, column="warehouse_number"):
    values = parse_warehouse_scope(warehouse)
    if not values:
        return query

    if len(values) == 1:
        predicate = f"{column} = {values[0]}"
    else:
        predicate = f"{column} IN ({', '.join(str(value) for value in values)})"

    return f"{query} AND {predicate}"
