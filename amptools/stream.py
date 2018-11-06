# stdlib imports
import warnings

# third party imports
import pandas as pd
import numpy as np
from obspy.core.stream import Stream
from obspy.geodetics import gps2dist_azimuth

# local imports
from amptools.process import filter_detrend
from pgm.station_summary import StationSummary


GAL_TO_PCTG = 1 / (9.8)

FILTER_FREQ = 0.02
CORNERS = 4

DEFAULT_IMTS = ['PGA', 'PGV', 'SA(0.3)', 'SA(1.0)', 'SA(3.0)']


def group_channels(streams):
    """Consolidate streams for the same event.

    Checks to see if there are channels for one event in different streams, and
    groups them into one stream. Then streams are checked for duplicate
    channels (traces).

    Args:
        streams (list): List of Stream objects.
    Returns:
        list: List of Stream objects.
    """
    # Return the original stream if there is only one
    if len(streams) <= 1:
        return streams

    # Get the all traces
    trace_list = []
    for stream in streams:
        for trace in stream:
            trace_list += [trace]

    # Create a list of duplicate traces and event matches
    duplicate_list = []
    match_list = []
    for idx1, trace1 in enumerate(trace_list):
        matches = []
        network = trace1.stats['network']
        station = trace1.stats['station']
        starttime = trace1.stats['starttime']
        endtime = trace1.stats['endtime']
        channel = trace1.stats['channel']
        location = trace1.stats['location']
        if 'units' in trace1.stats.standard:
            units = trace1.stats.standard['units']
        else:
            units = ''
        if 'process_level' in trace1.stats.standard:
            process_level = trace1.stats.standard['process_level']
        else:
            process_level = ''
        data = np.asarray(trace1.data)
        for idx2, trace2 in enumerate(trace_list):
            if idx1 != idx2 and idx1 not in duplicate_list:
                event_match = False
                duplicate = False
                # try:
                #     same_data = ((data == np.asarray(trace2.data)).all())
                # except AttributeError:
                #     same_data = (data == np.asarray(trace2.data))
                same_data = np.array_equal(data, trace2.data)
                if 'units' in trace2.stats.standard:
                    units2 = trace2.stats.standard['units']
                else:
                    units2 = ''
                if 'process_level' in trace2.stats.standard:
                    process_level2 = trace2.stats.standard['process_level']
                else:
                    process_level2 = ''
                if (
                    network == trace2.stats['network'] and
                    station == trace2.stats['station'] and
                    starttime == trace2.stats['starttime'] and
                    endtime == trace2.stats['endtime'] and
                    channel == trace2.stats['channel'] and
                    location == trace2.stats['location'] and
                    units == units2 and
                    process_level == process_level2 and
                    same_data
                ):
                    duplicate = True
                elif (
                    network == trace2.stats['network'] and
                    station == trace2.stats['station'] and
                    starttime == trace2.stats['starttime'] and
                    location == trace2.stats['location'] and
                    units == units2 and
                    process_level == process_level2
                ):
                    event_match = True
                if duplicate:
                    duplicate_list += [idx2]
                if event_match:
                    matches += [idx2]
        match_list += [matches]

    # Create an updated list of streams
    streams = []
    for idx, matches in enumerate(match_list):
        stream = Stream()
        grouped = False
        for match_idx in matches:
            if match_idx not in duplicate_list:
                if idx not in duplicate_list:
                    stream.append(trace_list[match_idx])
                    duplicate_list += [match_idx]
                    grouped = True
        if grouped:
            stream.append(trace_list[idx])
            duplicate_list += [idx]
            streams += [stream]

    # Check for ungrouped traces
    for idx, trace in enumerate(trace_list):
        if idx not in duplicate_list:
            stream = Stream()
            streams += [stream.append(trace)]
            warnings.warn('One channel stream:\n%s' % (stream), Warning)

    return streams


def streams_to_dataframe(streams, lat=None, lon=None, imtlist=None):
    """Extract peak ground motions from list of Stream objects.

    Note: The PGM columns underneath each channel will be variable
    depending on the units of the Stream being passed in (velocity
    sensors can only generate PGV) and on the imtlist passed in by
    user. Spectral acceleration columns will be formatted as SA(0.3)
    for 0.3 second spectral acceleration, for example.

    Args:
        streams (list): List of Stream objects.
        lat (float): Epicentral latitude.
        lon (float): Epicentral longitude
        imtlist (list): Strings designating desired PGMs to create
            in table.
    Returns:
        DataFrame: Pandas dataframe containing columns:
            - STATION Station code.
            - NAME Text description of station.
            - LOCATION Two character location code.
            - SOURCE Long form string containing source network.
            - NETWORK Short network code.
            - LAT Station latitude
            - LON Station longitude
            - DISTANCE Epicentral distance (km) (if epicentral lat/lon provided)
            - HHE East-west channel (or H1) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
            - HHN North-south channel (or H2) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
            - HHZ Vertical channel (or HZ) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).

    """
    # Validate imtlist, ensure everything is a valid IMT
    if imtlist is None:
        imtlist = DEFAULT_IMTS
    else:
        imtlist, invalid = _validate_imtlist(imtlist)
        if len(invalid):
            fmt = 'IMTs %s are invalid specifications. Skipping.'
            warnings.warn(fmt % (str(invalid)), Warning)

    # top level columns
    columns = ['STATION', 'NAME', 'SOURCE', 'NETID', 'LAT', 'LON']

    if lat is not None and lon is not None:
        columns.append('DISTANCE')

    # Check for common events and group channels
    streams = group_channels(streams)

    # Determine which channels should be created
    channels = []
    subchannels = []
    for stream in streams:
        for trace in stream:
            if trace.stats['channel'] not in channels:
                channels.append(trace.stats['channel'])
            if not len(subchannels):
                try:
                    units = trace.stats.standard['units']
                except Exception:
                    units = trace.stats['units']
                if units == 'acc':
                    subchannels = imtlist
                elif units == 'vel':
                    subchannels = list(set(['pgv']).intersection(set(imtlist)))
                else:
                    raise ValueError('Unknown units %s' % trace['units'])

    # Create dictionary to hold columns of basic data
    meta_dict = {}
    for column in columns:
        meta_dict[column] = []

    subcolumns = [''] * len(columns)
    subcolumns += subchannels * len(channels)

    # It's complicated to create a dataframe with a multiindex.
    # Create two arrays, one for top level columns, and another for "sub" columns.
    newchannels = []
    for channel in channels:
        newchannels += [channel] * len(subchannels)
    columns += newchannels

    dfcolumns = pd.MultiIndex.from_arrays([columns, subcolumns])
    dataframe = pd.DataFrame(columns=dfcolumns)

    # make sure we set the data types of all of the columns
    dtypes = {'STATION': str,
              'NAME': str,
              'SOURCE': str,
              'NETID': str,
              'LAT': np.float64,
              'LON': np.float64}

    if lat is not None:
        dtypes.update({'DISTANCE': np.float64})

    dataframe = dataframe.astype(dtypes)

    # create a dictionary for pgm data.  Because it is difficult to set columns
    # in a multiindex, we're creating dictionaries to create the channel
    # columns separately.
    channel_dicts = {}
    for channel in channels:
        channel_dicts[channel] = {}
        for subchannel in subchannels:
            channel_dicts[channel][subchannel] = []

    # loop over streams and extract data
    for stream in streams:
        for key in meta_dict.keys():
            if key == 'NAME':
                name_str = stream[0].stats['standard']['station_name']
                meta_dict[key].append(name_str)
            elif key == 'LAT':
                latitude = stream[0].stats['coordinates']['latitude']
                meta_dict[key].append(latitude)
            elif key == 'LON':
                longitude = stream[0].stats['coordinates']['longitude']
                meta_dict[key].append(longitude)
            elif key == 'STATION':
                meta_dict[key].append(stream[0].stats['station'])
            elif key == 'SOURCE':
                source = stream[0].stats.standard['source']
                meta_dict[key].append(source)
            elif key == 'NETID':
                meta_dict[key].append(stream[0].stats['network'])
            else:
                pass

        if lat is not None:
            dist, _, _ = gps2dist_azimuth(lat, lon,
                                          latitude,
                                          longitude)
            meta_dict['DISTANCE'].append(dist/1000)

        # process acceleration and store velocity traces
        for idx, trace in enumerate(stream):
            channel = trace.stats['channel']
            try:
                units = trace.stats.standard['units']
            except Exception:
                units = trace.stats['units']
            if units == 'acc':
                # do some basic data processing - if this has already been
                # done, it shouldn't hurt to repeat it.
                #TODO Check if data was processed/use new process routine
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    stream[idx] = filter_detrend(trace, taper_type='cosine',
                                                 taper_percentage=0.05,
                                                 filter_type='highpass',
                                                 filter_frequency=FILTER_FREQ,
                                                 filter_zerophase=True,
                                                 filter_corners=CORNERS)
            elif trace.stats['units'] == 'vel':
                # we only have a velocity channel
                pgv = np.abs(trace.max())
                channel_dicts[channel]['PGV'].append(pgv)
        # get station summary and assign values
        station = StationSummary.from_stream(stream, ['channels'],
                                             imtlist)
        spectral_streams = []
        tchannels = [t.stats.channel for t in stream]
        for channel in channels:
            if channel not in tchannels:
                for station_imt in imtlist:
                    channel_dicts[channel][station_imt].append(np.nan)
            else:
                for station_imt in imtlist:
                    imt_value = station.pgms[station_imt.upper()][channel]
                    channel_dicts[channel][station_imt].append(imt_value)
                    osc = station.oscillators[station_imt.upper()]
                    if station_imt.startswith('SA'):
                        spectral_streams.append(Stream(osc.select(channel=channel)[0]))

    # assign the non-channel specific stuff to dataframe
    for key, value in meta_dict.items():
        dataframe[key] = value

    # for each channel, assign peak values to dataframe
    for channel, channel_dict in channel_dicts.items():
        subdf = dataframe[channel].copy()
        for key, value in channel_dict.items():
            subdf[key] = value
        dataframe[channel] = subdf

    return (dataframe, spectral_streams)


def _validate_imtlist(imtlist):
    """Filter list of input IMTs, make sure each is a valid IMT spec.

    Args:
        imtlist (list): List of IMT strings.
    Returns:
        list: Filtered list of IMT strings
    """
    newimtlist = []
    invalid = []
    for imt in imtlist:
        if imt.upper() in ['PGA', 'PGV']:
            newimtlist.append(imt)
        else:
            if imt.upper().startswith('SA('):
                newimtlist.append(imt)
            else:
                invalid.append(imt)
    return (newimtlist, invalid)
