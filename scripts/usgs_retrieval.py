from dotenv import load_dotenv
load_dotenv()

import requests
import numpy as np
import pandas as pd
import datetime as dt
from pathlib import Path
from dataretrieval import waterdata

parameter_codes = {
    'discharge (cfs)':'00060',
    'gage height (ft)':'00065'
}

statistic_codes = {
    'daily mean':'00003',
    'continuous': '00011'
}

parameter_codes, _ = waterdata.get_reference_table("parameter-codes")
statistic_codes, _ = waterdata.get_reference_table("statistic-codes")


# -------------------- #
# functions
# -------------------- #


def get_continuous_flows(gage_id, start_year, end_year, start_month='01', end_month='01', start_day='01', end_day='02', timzezone='Etc/GMT+5', cache=Path('tmp')):
    '''
    Retrieve continuous flow data for a given USGS gage.
    -----
    ARGS
    - gage_id:          
    - start_year:       
    - end_year:         
    - start_month:      first month of desired timeseries
    - end_month:        
    - start_day:        
    - end_day:          
    - timezone:         
    - cache:            
    -----
    RETURNS
    - df:               
    '''
    parameter_code = parameter_codes['discharge (cfs)']
    dates = f'{start_year}-{start_month}-{start_day}T00:00:00Z/{end_year}-{end_month}-{end_day}T00:00:00Z'
        
    temp = []
    cache.mkdir(exist_ok=True)
        
    # for multi-year data
    if start_year-end_year>2:
        for year in np.arange[start_year:1:end_year]:
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
                df['request_time_EST'] = pd.Timestamp.now(tz='Etc/GMT+5').tz_localize(None).floor('min')
                df = df.drop(columns={'geometry'})
                df.to_parquet(year_path)
            temp.append(df)
                    
        # combine into single dataframe
        df = (pd.concat(temp, ignore_index=True)
            .drop_duplicates(subset=['time'])
            .sort_values('time')
            .reset_index(drop=True))
        
    else:
        year_path = cache / f'{gage_id}_{parameter_code}_{start_year}.parquet'
        if year_path.exists():
            df = pd.read_parquet(year_path)
            print(f'Requested data successfully read from cached file:\t{year_path}')
        else:
            df, md = waterdata.get_continuous(
                monitoring_location_id=gage_id,
                parameter_code=parameter_code,
                datetime=dates)
            df['request_url'] = md.url
            df['request_time_EST'] = pd.Timestamp.now(tz='Etc/GMT+5').tz_localize(None).floor('min')
            df = df.drop(columns={'geometry'})
            df.to_parquet(year_path)
            
    # convert to EST and rename columns
    df = df[['time','value']]
    df['time'] = (pd.to_datetime(df['time'], utc=True)
                .dt.tz_convert('Etc/GMT+5')
                .dt.tz_localize(None))
    df = df.rename(columns={'time':'Datetime_EST','value':'Flow_CFS'})
    df = df.set_index('Datetime_EST')
        
    return df