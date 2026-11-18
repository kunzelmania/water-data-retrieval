from dotenv import load_dotenv
load_dotenv()

import requests
import numpy as np
import pandas as pd
import datetime as dt
from pathlib import Path
from dataretrieval import waterdata

# USGS WDFN API parameter codes
parameter_codes, _ = waterdata.get_reference_table("parameter-codes")
parameter_codes = parameter_codes[['parameter_code','parameter_name','unit_of_measure']]

# USGS WDFN API statistic codes
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
    
    # set parameter code to continuous discharge, cfs
    mask = (parameter_codes['parameter_name'] == 'Discharge') & \
        parameter_codes['unit_of_measure'].str.contains('ft3/s', case=False, na=False)
    parameter_code = parameter_codes.loc[mask, 'parameter_code'].item()
    
    dates = f'{start_year}-{start_month}-{start_day}T00:00:00Z/{end_year}-{end_month}-{end_day}T00:00:00Z'
        
    temp = []
    cache.mkdir(exist_ok=True)
        
    # for multi-year data
    if end_year-start_year>2:
        for year in np.arange(start_year,end_year+1):
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
    df = df.rename(columns={'time':'Datetime_EST','value':'Flow_cfs'})
    df = df.set_index('Datetime_EST')
        
    return df

def get_continuous_levels(gage_id, start_year, end_year, start_month='01', end_month='01', start_day='01', end_day='02', timzezone='Etc/GMT+5', cache=Path('tmp')):
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
    
    # set parameter code to continous gage height, ft
    mask = (parameter_codes['parameter_name'] == 'Gage height') & \
        parameter_codes['unit_of_measure'].str.contains('ft', case=False, na=False)
    parameter_code = parameter_codes.loc[mask, 'parameter_code'].item()
    
    temp = []
    cache.mkdir(exist_ok=True)
        
    # for multi-year data
    if end_year-start_year>2:
        for year in np.arange(start_year,end_year+1):
            dates = f'{year}-{start_month}-{start_day}T00:00:00Z/{year+1}-{end_month}-{end_day}T00:00:00Z'
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
        dates = f'{start_year}-{start_month}-{start_day}T00:00:00Z/{end_year}-{end_month}-{end_day}T00:00:00Z'
        span = start_year if start_year == end_year else f'{start_year}-{end_year}'
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
            df['request_time_EST'] = pd.Timestamp.now(tz='Etc/GMT+5').tz_localize(None).floor('min')
            df = df.drop(columns={'geometry'})
            df.to_parquet(year_path)
            
    # convert to EST and rename columns
    df = df[['time','value']]
    df['time'] = (pd.to_datetime(df['time'], utc=True)
                .dt.tz_convert('Etc/GMT+5')
                .dt.tz_localize(None))
    df = df.rename(columns={'time':'Datetime_EST','value':'GageHeight_ft'})
    df = df.set_index('Datetime_EST')
        
    return df