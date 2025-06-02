from  datetime import datetime,timedelta
def get_costco_fiscal_info(input_date=None):
    # Default to today if no input date is provided
    if input_date is None:
        input_date = datetime.today()
    else:
        input_date = datetime.strptime(input_date, '%Y-%m-%d')

    # Extract year and determine fiscal year
    year = input_date.year
    fiscal_year = year + 1 if input_date.month >= 9 else year
    
    if input_date.month < 9:
        year = year - 1 

    # Find first Monday closest to September 1st of the current fiscal year
    fiscal_start = datetime(year, 9, 1)
    while fiscal_start.weekday() != 0:  # Monday is 0
        fiscal_start += timedelta(days=1)
        #print(fiscal_start)

    # Determine weeks since fiscal start
    days_since_start = (input_date - fiscal_start).days
    
        
    #print(days_since_start)
    weeks_since_start = days_since_start // 7
    #print(weeks_since_start)
    # Fiscal periods are 4 weeks long (except the last one)
    fiscal_period = min(12, (weeks_since_start // 4) + 1)

    return {
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period
    }
    