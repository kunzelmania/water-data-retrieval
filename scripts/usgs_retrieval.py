from jupyter_client.session import utc
import os
import numpy as np
import pandas as pd
from pathlib import Path
from dataretrieval import waterdata


# -------------------- #
# import USGS API codes
# -------------------- #


os.environ['API_USGS_PROGRESS'] = '0'
try:
    parameters, _ = waterdata.get_reference_table("parameter-codes")
    statistics, _ = waterdata.get_reference_table("statistic-codes")
except Exception as exc:
    print(f'Connection to the USGS Water Data API failed: {exc}')
    raise ConnectionError('Could not load USGS parameter and statistic codes') from exc
else:
    print('Connection to the USGS Water Data API successfully established.')


# --------------------------------------------------------------------------------------------------------

# -------------------- #
# helpers
# -------------------- #


DTYPES = ('continuous','mean','field observation')
DTYPES_USGS = {'continuous':'Instantaneous','mean':'Mean'}


# -------------------- #
# object classes
# -------------------- #


class StreamGage:
    '''
    Generates a stream gage object.
    ===================
    | gage_id:      gage/station name (format:  'USGS-12345678')
    | param_code:   parameter code for USGS water data
    | stat_code:    statistic code for USGS water data
    | data_type:    desired data type: 'continuous', 'mean', or 'field observation'
    | timezone:     optional argument for timezone conversion. defaults to UTC
    | cache:        filepath for storing retrieved data to minimize API requests
    '''
    
    def __init__(self, gage_id, param_code='00060', stat_code=None, data_type='continuous', timezone='UTC', cache=None):
        if not gage_id:
            raise ValueError('Please specify a gage ID.')
        gage_id = str(gage_id).strip().upper().removeprefix('USGS-')
        if not gage_id.isdigit():
            raise ValueError(f'Invalid USGS gage ID:    {gage_id!r}')
        self.gage_id = f'USGS-{gage_id}'
        self.param_code = str(param_code).zfill(5)
        self.stat_code = stat_code
        data_type = str(data_type).strip().lower()
        for name in DTYPES:
            if data_type.startswith(name[:4]):
                self.data_type = name
                break
        else:
            raise ValueError(f'Invalid data type:   {data_type!r}')
        self.timezone = timezone
        if cache is None:
            raise ValueError('Please specify a filepath for cached files.')
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.gage_info = None
        self.df = None
        self.md = None
        
    def retrieve(self, start_date, end_date):
        '''
        Retrieves water data from USGS WDFN API for a specified time range.
        ===================
        Arguments
        | start_date:   start date for requested data (format:  'YYYY-MM-DD')
        | end_date:     end date for requested data (format:  'YYYY-MM-DD')
        ===================
        Return
        | df:           pandas DataFrame containing requested data
        | md:           pandas DataFrame containing metadata for request
        '''
        
        # retrieve every time series USGS holds for this gage and parameter
        self.gage_info, _ = waterdata.get_combined_metadata(
            monitoring_location_id=self.gage_id,
            parameter_code=self.param_code,
            skip_geometry=True)
        
        # convert start and end dates to timezone-localized pandas Timestamps
        start = pd.Timestamp(start_date, tz=self.timezone)
        end = pd.Timestamp(end_date, tz=self.timezone)
        
        # check 1: end date falls after start date
        if end < start:
            raise ValueError('Specified end date is before specified start date.')
        
        # check 2: the gage has a time series of the requested type
        if not self.data_type.strip().lower().startswith(DTYPES):
            raise NotImplementedError(f'Invalid data type: {self.data_type!r}')
        series = self.gage_info.loc[self.gage_info['computation_identifier'] == DTYPES_USGS[self.data_type]]
        if series.empty:
            raise ValueError(
                f'Gage {self.gage_id} has no {self.data_type} data on record for parameter {self.param_code}.')
        
        # check 3: requested dates are within gage's period of record
        record_begin = series['begin'].min()
        record_end = series['end'].max()
        if start < record_begin or end > record_end:
            raise ValueError(
                f'Requested dates {start.date()} to {end.date()} fall outside the period of record for'
                f'{self.data_type} data at gage {self.gage_id}.\n'
                f'The gage\'s: period of record begins on {record_begin.tz_convert(self.timezone):%Y-%m-%d %H:%M} '
                f'and ends on {record_end.tz_convert(self.timezone):%Y-%m-%d %H:%M} (timezone: {self.timezone}).')
        
        # rename columns to match USGS parameter names and units
        row = parameters.loc[parameters['parameter_code'] == self.param_code]
        if row.empty:
            raise ValueError(f'Invalid parameter code: {self.param_code}')
        value_column = f"{row['parameter_name'].item()} ({row['unit_of_measure'].item()})"
        
        # for continuous data
        if self.data_type is 'continuous':
            # for long-term data (>3 years): one request and one cached file per year
            temp = []
            start_year = start_date.year
            end_year = end_date.dt.year
            
            if end_year-start_year>2:
                for year in np.arange(start_year,end_year+1):
                    dates = f'{year-1}-12-31T00:00:00Z/{year+1}-01-02T00:00:00Z'
                    year_path = self.cache / f'{self.gage_id}_{self.param_code}_{year}.parquet'
                                
                    # if data has been retrieved already, read it from cache
                    if year_path.exists():
                        df = pd.read_parquet(year_path)
                        print(f'Requested data successfully read from cached file:\t{year_path}')
                    else:
                        df, md = waterdata.get_continuous(
                            monitoring_location_id=self.gage_id,
                            parameter_code=self.param_code,
                            datetime=dates)
                        df['request_url'] = md.url
                        df['request_time'] = pd.Timestamp.now(tz=self.timezone).tz_localize(None).floor('min')
                        df['timezone'] = self.timezone
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
                year_path = self.cache / f'{self.gage_id}_{self.param_code}_{span}.parquet'
                if year_path.exists():
                    df = pd.read_parquet(year_path)
                    print(f'Requested data successfully read from cached file:\t{year_path}')
                else:
                    df, md = waterdata.get_continuous(
                        monitoring_location_id=self.gage_id,
                        parameter_code=self.param_code,
                        datetime=dates)
                    df['request_url'] = md.url
                    df['request_time'] = pd.Timestamp.now(tz=self.timezone).tz_localize(None).floor('min')
                    df['timezone'] = self.timezone
                    df = df.drop(columns={'geometry'})
                    df.to_parquet(year_path)
            
            # trim to requested dates
        
        elif self.data_type is 'mean':
            print(f'Retrieving daily means for {(row['parameter_name'].item()).lower()} '
                f'from gage {self.gage_id} for dates {start_date}-{end_date}.')
            # code for getting daily mean data here
        
        elif self.data_type is 'field observation':
            print(f'Retrieving field observations for {(row['parameter_name'].item()).lower()} '
                f'from gage {self.gage_id} for dates {start_date}-{end_date}.')
            # code for getting field observations here
        
        # convert timezones and rename columns
        df['time'] = (pd.to_datetime(df['time'], utc=True).dt.tz_convert(self.timezone).dt.tz_localize(None))
        df = df.rename(columns={'time':'DateTime','value':value_column})
        df = df.set_index('DateTime')
        
        # trim to the requested dates (local time): from start date 00:00
        # through the midnight that closes the end date, both inclusive
        start = pd.Timestamp(f'{start_year}-{start_month}-{start_day}')
        end = pd.Timestamp(f'{end_year}-{end_month}-{end_day}') + pd.Timedelta(days=1)
        df = df.loc[start:end]
        md = df[[value_column,'approval_status','request_time','timezone','request_url']]
        df = df[[value_column]]
        self.df = df.copy()
        self.md = md.copy()

        return df, md


# --------------------------------------------------------------------------------------------------------

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
    row = parameters.loc[parameters['parameter_code'] == parameter_code]
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