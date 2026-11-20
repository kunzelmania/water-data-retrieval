# usgs_retrieval

`scripts/usgs_retrieval.py` fetches water data for a single USGS stream gage from the USGS [Water Data for the Nation](https://waterdata.usgs.gov/) (WDFN) API, caches it locally, and plots it. It wraps the [`dataretrieval`](https://doi-usgs.github.io/dataretrieval-python/) package's `waterdata` module, and adds:

- one object per gage (`StreamGage`) that holds the gage's location and the latest data you fetched;
- checks that the gage actually has the data you ask for, and over the dates you ask for, *before* anything is downloaded;
- short names for common parameters and statistics (`'discharge'`, `'gage_height'`, `'mean'`, …), alongside USGS's own codes;
- a local cache of everything fetched, as Parquet files;
- times returned in the timezone of your choice;
- a quick-look plot (`gage.plot_data()`) that shows approved and provisional values apart.

It handles three kinds of USGS data:

| `data_type` | What it is | USGS service |
| --- | --- | --- |
| `'continuous'` | sensor readings, typically every 5 or 15 minutes | `get_continuous` |
| `'daily'` | one value per day for a chosen statistic (mean, max, min, …) | `get_daily` |
| `'field observation'` | individual measurements taken by USGS staff during site visits | `get_field_measurements` |

## Requirements

The environment is managed by [pixi](https://pixi.sh); `pixi install` in the repository folder sets it up. The script needs:

- Python 3.11 or later;
- `dataretrieval` 1.4 or later (below 2), `pandas`, `numpy`, `pyarrow` (for the Parquet cache) and `matplotlib` (for `plot_data`);
- a network connection, including when you only read cached data (see [Limitations](#limitations)).

### USGS API key (recommended)

A free personal key from the [USGS signup page](https://api.waterdata.usgs.gov/signup/) raises the API's rate limits; without one, requests still work but are limited much sooner. `dataretrieval` reads the key from the environment variable `API_USGS_PAT`.

Put it in the git-ignored `.env` file at the repository root:

```text
API_USGS_PAT=your-key-here
```

The script does not load `.env` itself. VS Code loads it for Python and Jupyter sessions by default; anywhere else, load it before importing the script (`from dotenv import load_dotenv; load_dotenv()`) or set the variable in your shell. Never commit the key.

## Quick start

With `scripts/` on your Python path (for example, a notebook in `scripts/`):

```python
from usgs_retrieval import StreamGage

gage = StreamGage('01646500', timezone='Etc/GMT+5', cache='cache')
gage.location                       # (-77.1276, 38.9498): longitude, latitude

# continuous discharge for 2020
gage.get_data('2020-01-01', '2020-12-31', 'continuous')
gage.df                             # the data
gage.md                             # metadata for each row

# daily mean discharge, then continuous gage height
gage.get_data('2020-01-01', '2020-12-31', 'daily', 'mean')
gage.get_data('2020-01-01', '2020-12-31', 'continuous', param_code='gage_height')

# other gages publish other daily statistics, e.g. daily minimum gage height:
# gage.get_data('2020-01-01', '2020-12-31', 'daily', 'min', 'gage_height')

# every field measurement of discharge from 2020 to the latest visit
gage.get_data('2020-01-01', data_type='field observation')

gage.plot_data()                                 # returns the figure
gage.plot_data(save_path='field_q.png')          # saves it instead
```

Importing the module downloads USGS's parameter and statistic code tables and prints a confirmation, so the first import takes a few seconds.

## `StreamGage`

```python
StreamGage(gage_id, timezone='UTC', cache=None)
```

| Argument | Description |
| --- | --- |
| `gage_id` | USGS site number, with or without the `USGS-` prefix, any case: `'01646500'`, `'USGS-01646500'` and `'usgs-01646500'` are the same gage. Pass it as a string: a number loses leading zeros. |
| `timezone` | Timezone for every date you pass in and every time returned. Any name pandas accepts, for example `'UTC'` (the default), `'America/New_York'` (with daylight saving) or `'Etc/GMT+5'` (US Eastern Standard Time all year). The `Etc/` names reverse the usual sign: `'Etc/GMT+5'` is UTC−5. |
| `cache` | Folder for cached data (required). It is created if it does not exist. |

Creating a gage asks USGS for its record, so a site number USGS does not know fails straight away.

### Attributes

Set when the gage is created:

| Attribute | Description |
| --- | --- |
| `gage_id` | Site ID as USGS writes it, `'USGS-01646500'` |
| `location` | `(longitude, latitude)`, the x, y order used by GeoJSON, shapely and geopandas: `Point(*gage.location)`. Reverse it for tools that expect latitude first. |
| `timezone`, `cache` | As passed in (`cache` as a `Path`) |

Set by each successful `get_data` call, and replaced by the next one:

| Attribute | Description |
| --- | --- |
| `df` | The data: one column named after USGS's parameter name and unit, e.g. `Discharge (ft3/s)`, indexed by `DateTime` in `timezone` |
| `md` | Metadata for each row of `df` (see [Output](#output)) |
| `data_type` | `'continuous'`, `'daily'` or `'field observation'` |
| `param_code`, `parameter`, `unit` | USGS parameter code, name and unit: `'00060'`, `'Discharge'`, `'ft3/s'` |
| `value_column` | The name of `df`'s column, `'Discharge (ft3/s)'` |
| `stat_code` | USGS statistic code for daily data (`'00003'`); `None` otherwise |
| `gage_info` | Every time series USGS holds for this gage and parameter, from `waterdata.get_combined_metadata` |

A call that fails changes none of these, so the gage still describes the last successful request.

## `get_data`

```python
gage.get_data(start, end=None, data_type='continuous', stat_code='00003', param_code='00060')
```

| Argument | Description |
| --- | --- |
| `start` | First date, `'YYYY-MM-DD'` (or anything pandas reads as a date), in the gage's `timezone` |
| `end` | Last date, included. Leave it out to run through the gage's latest data of that type. |
| `data_type` | `'continuous'`, `'daily'` or `'field observation'`. The first four letters are enough, in any case (`'cont'`, `'Daily'`, `'field'`). |
| `stat_code` | For daily data only: a USGS statistic code (`'00003'`) or a short name from `api_stats` (`'mean'`). Defaults to the daily mean. Ignored for other data types. |
| `param_code` | A USGS parameter code (`'00060'`) or a short name from `api_params` (`'discharge'`). Defaults to discharge. |

`get_data` returns nothing; the data is in `gage.df` and `gage.md`.

### Checks before downloading

`get_data` checks the request in this order, cheapest first, and raises `ValueError` with a message saying what is wrong:

1. `data_type` is one of the three types.
2. `end` is not before `start`.
3. The parameter is a known USGS code.
4. For daily data, the statistic is a known USGS code.
5. The gage has a time series of that type for that parameter, and for daily data of that statistic. The message lists the statistics the gage does have, e.g. `Daily statistics on record: 00003 (mean).`
6. The requested dates fall inside the gage's period of record for that series. The message gives the period of record.

Checks 1–4 need no network; checks 5 and 6 use one metadata request.

### Short names for codes

The module defines two dictionaries of short names; any USGS code also works directly.

`api_params`:

| Name | Code | USGS parameter (unit) |
| --- | --- | --- |
| `'water_temp'` | `00011` | Temperature, water (deg F) |
| `'air_temp'` | `00020` | Temperature, air (deg C) |
| `'wind_speed'` | `00035` | Wind speed (mph) |
| `'wind_dir'` | `00036` | Wind direction (Deg) |
| `'precip'` | `00045` | Precipitation (in) |
| `'discharge'` | `00060` | Discharge (ft3/s) |
| `'gage_height'` | `00065` | Gage height (ft) |

Most USGS gages record water temperature as `00010` (deg C) rather than `00011`, and air temperature in °F is `00021`; pass those codes directly when you need them.

`api_stats`, for daily data:

| Name | Code | | Name | Code |
| --- | --- | --- | --- | --- |
| `'max'` | `00001` | | `'sum'` | `00006` |
| `'min'` | `00002` | | `'mode'` | `00007` |
| `'mean'` | `00003` | | `'median'` | `00008` |
| `'am'` | `00004` (values between 00:01 and 12:00) | | `'std'` | `00009` |
| `'pm'` | `00005` (values between 12:01 and 24:00) | | `'variance'` | `00010` |

USGS's full lists are loaded with the module as `parameters` and `statistics`; the statistic codes are also listed on [USGS's statistic code page](https://help.waterdata.usgs.gov/code/stat_cd_nm_query?stat_nm_cd=%25&fmt=html).

## Output

`gage.df` has one column, named after USGS's own parameter name and unit, and a `DateTime` index:

```text
                           Discharge (ft3/s)
DateTime
2020-01-01 00:00:00-05:00             7610.0
2020-01-02 00:00:00-05:00             6730.0
2020-01-03 00:00:00-05:00             6350.0
```

`gage.md` has the same index and these columns:

| Column | Description |
| --- | --- |
| *value column* | The value again, so each metadata row reads on its own |
| `approval_status` | `Approved`, or `Provisional` for recent data USGS has not yet reviewed. Provisional values can change. |
| `request_time` | When the data was fetched from USGS, in `timezone` |
| `timezone` | The timezone the data was fetched with |
| `request_url` | The API request that returned the data |

Field observations add `measurement_rated` (USGS's rating of the measurement's accuracy: Good, Fair or Poor), `reading_type`, `observing_procedure`, `control_condition`, `qualifier`, `field_visit_id` and `field_measurement_id`.

### Times and timezones

All times keep their timezone, in the gage's `timezone`:

- **Continuous** readings arrive in UTC and are converted.
- **Daily** values are dated by local calendar day, so they are labelled with the timezone without being converted; the value for 2020-01-01 stays on 2020-01-01.
- **Field observations** arrive as a date and a UTC time of day, which are combined and converted.

The selected period runs from midnight on `start` through the midnight that closes `end`, both included; daily data has one row per date from `start` to `end`. If a tool you export to needs plain timestamps without a timezone, use `gage.df.index.tz_localize(None)`.

## Caching

Every download is saved in the `cache` folder as a Parquet file, and the same request later reads the file instead of calling USGS. File names give the gage, parameter, data type (with the statistic for daily data) and years:

```text
USGS-01646500_00060_continuous_2020.parquet
USGS-01646500_00060_daily_00003_2020.parquet
USGS-01646500_00060_field_observation_2018-2020.parquet
```

Continuous requests longer than three years are fetched and cached one year at a time. All other requests are fetched as one file for the whole span of years.

**Cached files are never refreshed.** This is deliberate: each file is a record of the data as USGS published it on the day it was fetched (`request_time` in `md`). It means:

- a file covering the current year stops at the day it was fetched, including when `end` is left out;
- `Provisional` values are kept as they were, even after USGS approves or revises them.

To get current data, delete the affected file (only that year is fetched again), or use a new `cache` folder (everything is fetched again).

## `plot_data`

```python
gage.plot_data(size=(16, 9), save_path=None)
```

Plots the data from the latest `get_data` call, with a title naming the data type, parameter, gage and dates. Discharge is plotted on a log scale.

Continuous and daily data are drawn as lines, styled by each value's `approval_status` in `gage.md`:

| Line | Values |
| --- | --- |
| solid blue | `Approved` |
| dashed purple | everything else, which in practice is `Provisional` |

The dashed line starts at the last approved value, so the two lines meet without a gap. A line is drawn only when the data has values of that status, and a legend in the upper right names the lines shown. A gap in either line is a missing value in the data.

Field observations are drawn as red points, without regard to status.

| Argument | Description |
| --- | --- |
| `size` | Figure size in inches, `(width, height)` |
| `save_path` | File to save the figure to. Leave it out to get the figure back instead. |

- Without `save_path`, it returns the matplotlib figure (in Jupyter, it displays once).
- With `save_path`, it saves the figure at 300 dpi and returns a message giving the path.

It raises `ValueError` if `get_data` has not been run.

## Limitations

- **The network is needed even for cached data.** Importing the module downloads the code tables, and creating a gage looks up its location.
- **Several sensors can be mixed.** When a gage has more than one series of the same type for a parameter (for example two water temperature sensors), they are fetched together and, where they share a time, only the first value is kept. Discharge at most gages has one series. `gage.gage_info` lists the series a gage has.
- **Gaps between series are not detected.** Check 6 uses the earliest start and latest end across a gage's series, so a request that falls between two series passes the check and returns little or no data.
- **Continuous data includes every continuous series,** including `Decumulated` ones that some precipitation gages publish alongside instantaneous readings.
- **Field observation dates are assumed to be UTC dates.** USGS sends the date and the UTC time of day separately; this has not been confirmed for measurements made late in the local day.
- **The timezone is only checked when `get_data` runs,** so a misspelled timezone fails then rather than when the gage is created.
- **Importing the module turns off `dataretrieval`'s progress bars** for the rest of the session (`API_USGS_PROGRESS=0`).

## References

- [Water Data for the Nation](https://waterdata.usgs.gov/), USGS's public data site
- [USGS Water Data API key signup](https://api.waterdata.usgs.gov/signup/)
- [`dataretrieval` documentation](https://doi-usgs.github.io/dataretrieval-python/) and its [Water Data demo](https://doi-usgs.github.io/dataretrieval-python/examples/WaterData_demo.html)
- [USGS parameter codes](https://api.waterdata.usgs.gov/ogcapi/v0/collections/parameter-codes/items)
- [USGS statistic codes](https://api.waterdata.usgs.gov/ogcapi/v0/collections/statistic-codes/items)
