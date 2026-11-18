import os
import numpy as np
import pandas as pd
from pathlib import Path
from dataretrieval import waterdata


# -------------------- #
# import USGS API codes
# -------------------- #


_previous_progress = os.environ.get('API_USGS_PROGRESS')
os.environ['API_USGS_PROGRESS'] = '0'
try:
    parameter_codes, _ = waterdata.get_reference_table("parameter-codes")
    parameter_codes = parameter_codes[['parameter_code','parameter_name','unit_of_measure']]
    statistic_codes, _ = waterdata.get_reference_table("statistic-codes")
except Exception as exc:
    print(f'Connection to the USGS Water Data API failed: {exc}')
    raise ConnectionError('Could not load USGS parameter and statistic codes') from exc
else:
    print('Connection to the USGS Water Data API successfully established.')
finally:
    # put the progress setting back as it was before this module was imported
    if _previous_progress is None:
        del os.environ['API_USGS_PROGRESS']
    else:
        os.environ['API_USGS_PROGRESS'] = _previous_progress


# -------------------- #
# functions
# -------------------- #


def get_continuous_data(gage_id, start_year, end_year, parameter_code='00060', start_month='01', end_month='12', start_day='01', end_day='31', timezone='Etc/GMT+5', cache=Path('tmp')):
    '''
    Retrieve continuous data for a given USGS gage.
    -----
    ARGS
    - gage_id:          USGS station/gage ID
    - parameter_code:   USGS WDFN parameter code
    - start_year:       first year of data to be retrieved
    - end_year:         last year of data to be retrieved
    - start_month:      first year of data to be retrieved
    - end_month:        last year of data to be retrieved
    - start_day:        first year of data to be retrieved
    - end_day:          last year of data to be retrieved
    - timezone:         timezone
    - cache:            path for cached files to be stored
    -----
    RETURNS
    - df:               pandas DataFrame containing timeseries of requested data
    - md:               pandas DataFrame containing metadata for requested data
    '''
    
    # name the value column after USGS's own parameter name and unit,
    # e.g. 00060 -> 'Discharge (ft3/s)', 00065 -> 'Gage height (ft)'
    parameter_code = str(parameter_code).zfill(5)
    row = parameter_codes.loc[parameter_codes['parameter_code'] == parameter_code]
    if row.empty:
        raise ValueError(f'Unknown USGS parameter code: {parameter_code}')
    value_column = f"{row['parameter_name'].item()} ({row['unit_of_measure'].item()})"

    temp = []
    cache.mkdir(exist_ok=True)
        
    # always fetch whole calendar years, padded by a day on each side so
    # the local-time trim below has data at both ends in any timezone

    # for long-term data (>3 years): one request and one cached file per year
    if end_year-start_year>2:
        for year in np.arange(start_year,end_year+1):
            dates = f'{year-1}-12-31T00:00:00Z/{year+1}-01-02T00:00:00Z'
            year_path = cache / f'{gage_id}_{parameter_code}_{year}.parquet'
                        
            # if data has been retrieved already, read it from cache
            if year_path.exists():
                df = pd.read_parquet(year_path)
                print(f'Requested data successfully read from cached file:\t{year_path}')
            else:
                df, md = waterdata.get_continuous(
                    monitoring_location_id=gage_id,
                    parameter_code=parameter_code,
                    datetime=dates)
                df['request_url'] = md.url
                df['request_time'] = pd.Timestamp.now(tz=timezone).tz_localize(None).floor('min')
                df['timezone'] = timezone
                df = df.drop(columns={'geometry'})
                df.to_parquet(year_path)
            
            temp.append(df)
            
        # combine into single dataframe
        df = (pd.concat(temp, ignore_index=True)
            .drop_duplicates(subset=['time'])
            .sort_values('time')
            .reset_index(drop=True))
    
    # for short-term data (<=3 years): one request and one cached file
    else:
        dates = f'{start_year-1}-12-31T00:00:00Z/{end_year+1}-01-02T00:00:00Z'
        span = f'{start_year}' if start_year == end_year else f'{start_year}-{end_year}'
        year_path = cache / f'{gage_id}_{parameter_code}_{span}.parquet'
        if year_path.exists():
            df = pd.read_parquet(year_path)
            print(f'Requested data successfully read from cached file:\t{year_path}')
        else:
            df, md = waterdata.get_continuous(
                monitoring_location_id=gage_id,
                parameter_code=parameter_code,
                datetime=dates)
            df['request_url'] = md.url
            df['request_time'] = pd.Timestamp.now(tz=timezone).tz_localize(None).floor('min')
            df['timezone'] = timezone
            df = df.drop(columns={'geometry'})
            df.to_parquet(year_path)
            
    # convert timezones and rename columns
    df['time'] = (pd.to_datetime(df['time'], utc=True)
                .dt.tz_convert(timezone)
                .dt.tz_localize(None))
    df = df.rename(columns={'time':'DateTime','value':value_column})
    df = df.set_index('DateTime')

    # trim to the requested dates (local time): from start date 00:00
    # through the midnight that closes the end date, both inclusive
    start = pd.Timestamp(f'{start_year}-{start_month}-{start_day}')
    end = pd.Timestamp(f'{end_year}-{end_month}-{end_day}') + pd.Timedelta(days=1)
    df = df.loc[start:end]
    md = df[[value_column,'approval_status','request_time','timezone','request_url']]
    df = df[[value_column]]

    return df, md