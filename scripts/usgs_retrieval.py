'''
usgs_retrieval
===================
This library is intended to simplify data collection, analysis, and vizualization
from the USGS' Water Data for the Nation (WDFN) database. A free USGS API key is
required to use this library. See `docs/usgs_retrieval.md` for more details.
'''

import os
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt, ticker as mticker, dates as mdates
from pathlib import Path
from dataretrieval import waterdata


# =================== #
# import USGS API codes
# =================== #


os.environ['API_USGS_PROGRESS'] = '0'
try:
    parameters, _ = waterdata.get_reference_table("parameter-codes")
    statistics, _ = waterdata.get_reference_table("statistic-codes")
except Exception as exc:
    print(f'Connection to the USGS Water Data API failed: {exc}')
    raise ConnectionError('Could not load USGS parameter and statistic codes') from exc
else:
    print('Connection to the USGS Water Data API successfully established.')


# =================== #
# helpers
# =================== #


# each observation type, with its name in the 'data_type' column of USGS's time-series metadata
api_obs = {
    'continuous':'Continuous values',
    'daily':'Daily values',
    'field observation':'Field measurements'}
api_params = {
    'water_temp':'00011',   # deg F
    'air_temp':'00020',     # deg F
    'wind_speed':'00035',   # mph
    'wind_dir':'00036',     # deg
    'precip':'00045',       # in.
    'discharge':'00060',    # cfs
    'gage_height':'00065'}  # ft
api_stats = {
    'max':'00001',
    'min':'00002',
    'mean':'00003',
    'am':'00004',           # values taken between 0001 and 1200
    'pm':'00005',           # values taken between 1201 and 2400
    'sum':'00006',
    'mode':'00007',
    'median':'00008',
    'std':'00009',
    'variance':'00010'}
file_formats = {
    'excel':'.xlsx',
    'csv':'.csv',
    'dat':'.dat',
    'parquet':'.parquet'}


def _stat_label(stat_code):
    '''
    Statistic code with its name, e.g. '00002 (min)': the api_stats key
    where there is one, otherwise USGS's own statistic name.
    '''
    for key, code in api_stats.items():
        if code == stat_code:
            return f'{stat_code} ({key})'
    row = statistics.loc[statistics['statistic_code'] == stat_code]
    return f'{stat_code} ({row["statistic_name"].item().lower()})' if not row.empty else stat_code


# -------------------- #
# object classes
# -------------------- #


class StreamGage:
    '''
    Creates a stream gage object from USGS WDFN data.
    ===================
    | gage_id:      gage/station name (format:  'USGS-12345678')
    | timezone:     optional argument for timezone conversion. defaults to UTC
    | cache:        filepath for storing retrieved data to minimize API requests
    '''

    def __init__(self, gage_id, timezone='UTC', cache=None):
        if not gage_id:
            raise ValueError('Please specify a gage ID.')
        gage_id = str(gage_id).strip().upper().removeprefix('USGS-')
        if not gage_id.isdigit():
            raise ValueError(f'Invalid USGS gage ID:    {gage_id!r}')
        self.gage_id = f'USGS-{gage_id}'

        # gage location as (longitude, latitude), the x, y order of GIS tools
        # (GeoJSON, shapely, geopandas); from USGS's monitoring-location record
        site, _ = waterdata.get_monitoring_locations(monitoring_location_id=self.gage_id)
        if site.empty:
            raise ValueError(f'USGS has no monitoring location {self.gage_id}.')
        geometry = site['geometry'].iloc[0]
        # a [lon, lat] list without geopandas; a shapely Point with it
        self.location = (geometry.x, geometry.y) if hasattr(geometry, 'x') else tuple(geometry)

        self.timezone = timezone
        if cache is None:
            raise ValueError('Please specify a filepath for cached files.')
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)

        # set by get_data()
        self.param_code = None
        self.parameter = None
        self.unit = None
        self.value_column = None
        self.stat_code = None
        self.data_type = None
        self.gage_info = None
        self.df = None
        self.md = None

    def get_data(self, start, end=None, data_type='continuous', stat_code='00003', param_code='00060'):
        '''
        Retrieves water data from USGS WDFN API for a specified time range.
        ===================
        Arguments
        | start:        start date for requested data (format:  'YYYY-MM-DD')
        | end:          end date for requested data (format:  'YYYY-MM-DD'); optional:
        |               when left out, data runs through the end of the gage's record
        | data_type:    desired data type: 'continuous', 'daily', or 'field observation'
        | stat_code:    USGS statistic code or a key in api_stats (e.g., '00003' or 'mean');
        |               used for 'daily' only. defaults to '00003' (mean)
        | param_code:   USGS parameter code or a key in api_params (e.g., '00060' or 'discharge');
        |               defaults to '00060' (discharge)
        '''

        # the checks below work on local variables; the object's attributes
        # are only updated once every check has passed (see end of checks)

        # check 1: data type is one of obs_types (accepts its first four letters, any case)
        data_type = str(data_type).strip().lower()
        for name in api_obs:
            if data_type.startswith(name[:4]):
                data_type = name
                break
        else:
            raise ValueError(f'Invalid data type:   {data_type!r}')

        # check 2: end date falls after start date (both in the gage's timezone);
        # an end left out stays None here and is set from the gage's record after check 6
        start = pd.Timestamp(start, tz=self.timezone)
        if end is not None:
            end = pd.Timestamp(end, tz=self.timezone)
            if end < start:
                raise ValueError('Specified end date is before specified start date.')

        # check 3: parameter is a known USGS code, given as a code ('00060')
        # or as a key in api_params ('discharge'); its name and unit become
        # the value column's name
        requested = str(param_code).strip().lower()
        param_code = api_params.get(requested, requested.zfill(5))
        row = parameters.loc[parameters['parameter_code'] == param_code]
        if row.empty:
            raise ValueError(f'Invalid parameter code: {requested!r}')
        parameter = row['parameter_name'].item()
        unit = row['unit_of_measure'].item()

        # check 4: for daily data, statistic is a known USGS code, given as a
        # code ('00003') or as a key in api_stats ('mean'); other data types ignore it
        if data_type == 'daily':
            requested = str(stat_code).strip().lower()
            stat_code = api_stats.get(requested, requested.zfill(5))
            if stat_code not in statistics['statistic_code'].values:
                raise ValueError(f'Invalid statistic code: {requested!r}')
        else:
            stat_code = None

        # data type as written in cached file names, with the statistic for
        # daily data ('daily_00003', 'continuous', 'field_observation')
        obs_filename = data_type.replace(' ', '_') + (f'_{stat_code}' if stat_code else '')

        # retrieve every time series USGS holds for this gage and parameter
        gage_info, _ = waterdata.get_combined_metadata(
            monitoring_location_id=self.gage_id,
            parameter_code=param_code,
            skip_geometry=True)

        # check 5: the gage has a time series of the requested type
        # (for daily data, of the requested statistic)
        series = gage_info.loc[gage_info['data_type'] == api_obs[data_type]]
        if series.empty:
            raise ValueError(
                f'Gage {self.gage_id} has no {data_type} data on record for parameter {param_code}.')
        if data_type == 'daily':
            available = sorted(series['statistic_id'].dropna().unique())
            series = series.loc[series['statistic_id'] == stat_code]
            if series.empty:
                raise ValueError(
                    f'Gage {self.gage_id} has no daily data for statistic {_stat_label(stat_code)} '
                    f'on record for parameter {param_code}. '
                    f'Daily statistics on record: {", ".join(_stat_label(s) for s in available)}.')

        # check 6: requested dates are within gage's period of record
        # ('begin' and 'end' arrive in UTC; converted here to the gage's timezone)
        record_begin = series['begin'].min().tz_convert(self.timezone)
        record_end = series['end'].max().tz_convert(self.timezone)
        if start < record_begin or (end is not None and end > record_end):
            requested_end = end.date() if end is not None else 'end of record'
            raise ValueError(
                f'Requested dates {start.date()} to {requested_end} fall outside the period of record for '
                f'{data_type} data at gage {self.gage_id}.\n'
                f'The gage\'s period of record begins on {record_begin:%Y-%m-%d %H:%M} '
                f'and ends on {record_end:%Y-%m-%d %H:%M} (timezone: {self.timezone}).')

        # no end given: run through the gage's latest data of this type
        if end is None:
            end = record_end

        # all checks passed: record this request on the object
        self.data_type = data_type
        self.param_code = param_code
        self.parameter = parameter
        self.unit = unit
        self.value_column = f'{parameter} ({unit})'
        self.stat_code = stat_code
        self.gage_info = gage_info

        start_year = start.year
        end_year = end.year

        # for continuous data
        if self.data_type == 'continuous':
            print(f'Retrieving continuous values for {self.parameter.lower()} '
                f'from gage {self.gage_id} for dates {start.date()} to {end.date()}...')
            
            temp = []
            c=0
            
            # for long-term data (>3 years): one request and one cached file per year
            if end_year-start_year>2:
                for year in np.arange(start_year,end_year+1):
                    dates = f'{year-1}-12-31T00:00:00Z/{year+1}-01-02T00:00:00Z'
                    year_path = self.cache / f'{self.gage_id}_{self.param_code}_{obs_filename}_{year}.parquet'
                                
                    # if data has been retrieved already, read it from cache
                    if year_path.exists():
                        df = pd.read_parquet(year_path)
                        print(f'Requested data successfully read from cached file:\t{year_path}')
                    else:
                        df, md = waterdata.get_continuous(
                            monitoring_location_id=self.gage_id,
                            parameter_code=self.param_code,
                            time=dates)
                        df['request_url'] = md.url
                        df['request_time'] = pd.Timestamp.now(tz=self.timezone).tz_localize(None).floor('min')
                        df['timezone'] = self.timezone
                        df = df.drop(columns={'geometry'})
                        df.to_parquet(year_path)
                        c=1
                    
                    temp.append(df)
                    
                # combine into single dataframe
                df = (pd.concat(temp, ignore_index=True)
                    .drop_duplicates(subset=['time'])
                    .sort_values('time')
                    .reset_index(drop=True))
                
                if c == 1: print('Requested data successfully retrieved from USGS database.')
                
            # for short-term data (<=3 years): one request and one cached file
            else:
                dates = f'{start_year-1}-12-31T00:00:00Z/{end_year+1}-01-02T00:00:00Z'
                span = f'{start_year}' if start_year == end_year else f'{start_year}-{end_year}'
                year_path = self.cache / f'{self.gage_id}_{self.param_code}_{obs_filename}_{span}.parquet'
                if year_path.exists():
                    df = pd.read_parquet(year_path)
                    print(f'Requested data successfully read from cached file:\t{year_path}')
                else:
                    df, md = waterdata.get_continuous(
                        monitoring_location_id=self.gage_id,
                        parameter_code=self.param_code,
                        time=dates)
                    df['request_url'] = md.url
                    df['request_time'] = pd.Timestamp.now(tz=self.timezone).tz_localize(None).floor('min')
                    df['timezone'] = self.timezone
                    df = df.drop(columns={'geometry'})
                    df.to_parquet(year_path)
                    c=1

                if c == 1: print('Requested data successfully retrieved from USGS database.')
            
        # for daily statistics
        if self.data_type == 'daily':
            print(f'Retrieving daily values for {self.parameter.lower()} '
                f'from gage {self.gage_id} for dates {start.date()} to {end.date()}...')
            
            dates = f'{start_year-1}-12-31/{end_year+1}-01-02'
            span = f'{start_year}' if start_year == end_year else f'{start_year}-{end_year}'
            year_path = self.cache / f'{self.gage_id}_{self.param_code}_{obs_filename}_{span}.parquet'
            
            if year_path.exists():
                df = pd.read_parquet(year_path)
                print(f'Requested data successfully read from cached file:\t{year_path}')
            else:
                df, md = waterdata.get_daily(
                    monitoring_location_id=self.gage_id,
                    parameter_code=self.param_code,
                    statistic_id=self.stat_code,
                    time=dates)
                df['request_url'] = md.url
                df['request_time'] = pd.Timestamp.now(tz=self.timezone).tz_localize(None).floor('min')
                df['timezone'] = self.timezone
                df = df.drop(columns={'geometry'})
                df.to_parquet(year_path)
                
            # drop duplicates
            df = (df.drop_duplicates(subset=['time'])
                .sort_values('time')
                .reset_index(drop=True))
            
        # for field observations
        if self.data_type == 'field observation':
            print(f'Retrieving field observations for {self.parameter.lower()} '
                f'from gage {self.gage_id} for dates {start.date()} to {end.date()}...')
            
            # field measurements are sparse (a few site visits a year), so one
            # request and one cached file cover any span of years
            dates = f'{start_year-1}-12-31/{end_year+1}-01-02'
            span = f'{start_year}' if start_year == end_year else f'{start_year}-{end_year}'
            year_path = self.cache / f'{self.gage_id}_{self.param_code}_{obs_filename}_{span}.parquet'

            if year_path.exists():
                df = pd.read_parquet(year_path)
                print(f'Requested data successfully read from cached file:\t{year_path}')
            else:
                df, md = waterdata.get_field_measurements(
                    monitoring_location_id=self.gage_id,
                    parameter_code=self.param_code,
                    time=dates,
                    skip_geometry=True)
                # USGS sends the date in 'time' and the clock time, in UTC, in
                # 'time_of_day' (e.g. '2020-04-29' and '14:00:49+00:00'): combine
                # them into one UTC timestamp before caching
                df['time'] = pd.to_datetime(
                    df['time'].dt.strftime('%Y-%m-%d') + ' ' + df['time_of_day'], utc=True)
                df['request_url'] = md.url
                df['request_time'] = pd.Timestamp.now(tz=self.timezone).tz_localize(None).floor('min')
                df['timezone'] = self.timezone
                df.to_parquet(year_path)
                print('Requested data successfully retrieved from USGS database.')

            # two readings can share a time, so sort without dropping duplicates
            # (each row has its own 'field_measurement_id')
            df = df.sort_values('time').reset_index(drop=True)

        # put times in the gage's timezone, keeping the timezone on them
        if self.data_type == 'daily':
            # daily values are dated by local calendar day, not by a UTC moment:
            # attach the timezone without converting, or each day would shift
            df['time'] = pd.to_datetime(df['time']).dt.tz_localize(self.timezone)
        else:
            # continuous times arrive in UTC; field observation times are
            # combined into UTC timestamps in their branch (date assumed UTC for now)
            df['time'] = pd.to_datetime(df['time'], utc=True).dt.tz_convert(self.timezone)

        # rename columns
        df = df.rename(columns={'time':'DateTime','value':self.value_column})
        df = df.set_index('DateTime')

        # trim to the requested dates (local time), both inclusive: daily values
        # are one row per date; continuous and field readings run from start
        # date 00:00 through the midnight that closes the end date
        if self.data_type == 'daily':
            df = df.loc[start.normalize():end.normalize()]
        else:
            df = df.loc[start.normalize():end.normalize() + pd.Timedelta(days=1)]
        if df.empty:
            print(f'No {self.data_type} data for {self.parameter.lower()} at gage {self.gage_id} '
                f'between {start.date()} and {end.date()}.')

        # metadata: what every data type has, plus what describes a field measurement
        md_columns = [self.value_column,'approval_status','request_time','timezone','request_url']
        if self.data_type == 'field observation':
            md_columns += ['measurement_rated','reading_type','observing_procedure',
                'control_condition','qualifier','field_visit_id','field_measurement_id']
        md = df[md_columns]
        df = df[[self.value_column]]
        self.df = df.copy()
        self.md = md.copy()
        
    def plot_data(self, size=(16,9), save_path=None):
        '''
        Generate a plot for a single StreamGage object.
        ===================
        | size:         plot size (optional; if omitted, default = 16x9)
        | save_path:    filepath to save the plot to (optional; if omitted, the plot is closed)
        ===================
        Return:         StreamGage plot (or save_path, if provided)
        '''
        
        if self.df is None:
            raise ValueError(f'No data set for object `{self.gage_id}`; run `get_data()` first.')
        
        data = self.df[self.value_column]
        index = self.df.index
        # first and last dates as YYYY-MM-DD, for the title
        first, last = f'{index[0]:%Y-%m-%d}', f'{index[-1]:%Y-%m-%d}'
        
        fig, ax = plt.subplots(figsize=size, constrained_layout=True)

        def plot_by_status():
            '''Approved values as a solid blue line, the rest (provisional) as a dashed purple line.'''
            # numpy arrays, so masks line up by position even if a time repeats in the index
            approved = self.md['approval_status'].to_numpy() == 'Approved'
            provisional = ~approved
            # widen the provisional mask by one value on each side, so the dashed
            # line joins the solid line instead of leaving a gap where they meet
            joined = provisional.copy()
            joined[:-1] |= provisional[1:]
            joined[1:] |= provisional[:-1]
            if approved.any():
                ax.plot(index, data.where(approved), color='#174A7C', label='Approved')
            if provisional.any():
                ax.plot(index, data.where(joined), color='#6A1148', linestyle='--', label='Provisional')
            ax.legend(loc='upper right')

        if self.data_type=='continuous':
            title = f'Continuous {self.parameter} data @ station {self.gage_id} ({first} to {last})'
            plot_by_status()
        elif self.data_type=='daily':
            # statistic's name from api_stats (e.g. '00003' -> 'mean'); the code itself if it has none
            key = next((k for k, v in api_stats.items() if v == self.stat_code), self.stat_code)
            title = f'Daily {key} {self.parameter} data @ station {self.gage_id} ({first} to {last})'
            plot_by_status()
        elif self.data_type=='field observation':
            title = f'Field observations of {self.parameter} data @ station {self.gage_id} ({first} to {last})'
            ax.plot(index, data, 'o', color='#Df4D4D')
            
        ax.set_xlabel(f'Date ({self.timezone})')
        ax.set_ylabel(self.value_column)
        if self.parameter.lower() == 'discharge':
            ax.set_yscale('log')
        ax.grid(linestyle=':', linewidth=0.5, alpha=0.5)
        fig.suptitle(title, fontsize=18)
            
        # return plot and, if provided, save figure to save_path
        if save_path is not None:
            fig.savefig(save_path, dpi=300, bbox_inches='tight')
            plt.close(fig)
            return f'Figure saved to: {Path(save_path)}'
        plt.close(fig)
        return fig

