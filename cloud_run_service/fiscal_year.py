"""
Costco fiscal calendar utilities.

Fiscal year starts the first Monday on or after September 1.
Has 12 fiscal periods, each 4 weeks long (last may be shorter).
A fiscal_year of "2026" means the year that ends in calendar 2026 —
i.e. the year that started in Sep 2025.
"""

from datetime import datetime, timedelta


def get_costco_fiscal_info(input_date: str | None = None) -> dict:
    """
    Returns the fiscal_year and fiscal_period for a given date.

    Args:
      input_date: ISO date string 'YYYY-MM-DD', or None for today.

    Returns:
      {"fiscal_year": int, "fiscal_period": int (1-12)}
    """
    if input_date is None:
        d = datetime.today()
    else:
        d = datetime.strptime(input_date, "%Y-%m-%d")

    year = d.year
    fiscal_year = year + 1 if d.month >= 9 else year
    if d.month < 9:
        year -= 1

    # First Monday on or after September 1 of the fiscal start year
    fiscal_start = datetime(year, 9, 1)
    while fiscal_start.weekday() != 0:  # Monday is 0
        fiscal_start += timedelta(days=1)

    days_since_start = (d - fiscal_start).days
    weeks_since_start = days_since_start // 7
    fiscal_period = min(12, (weeks_since_start // 4) + 1)

    return {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period}