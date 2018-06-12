#!/usr/bin/env python

# stdlib imports
from datetime import datetime
import re
import os.path

# third party
from obspy.core.trace import Trace
from obspy.core.stream import Stream
from obspy.core.trace import Stats
import numpy as np

TEXT_HDR_ROWS = 17
TIMEFMT = '%Y/%m/%d %H:%M:%S'
COLS_PER_LINE = 8


def is_knet(filename):
    """Check to see if file is a Japanese KNET strong motion file.

    Args:
        filename (str): Path to possible GNS V1 data file.
    Returns:
        bool: True if GNS V1, False otherwise.
    """
    if not os.path.isfile(filename):
        return False
    with open(filename, 'rt') as f:
        lines = [next(f) for x in range(TEXT_HDR_ROWS)]
    if lines[0].startswith('Origin Time') and lines[5].startswith('Station Code'):
        return True
    return False


def read_knet(filename):
    """Read Japanese KNET strong motion file.

    Args:
        filename (str): Path to possible KNET data file.
        kwargs (ref): Other arguments will be ignored.
    Returns:
        Stream: Obspy Stream containing three channels of acceleration data (cm/s**2).
    """
    if not is_knet(filename):
        raise Exception('%s is not a valid KNET file' % filename)

    # Parse the header portion of the file
    with open(filename, 'rt') as f:
        lines = [next(f) for x in range(TEXT_HDR_ROWS)]

    hdr = {}
    coordinates = {}
    standard = {}
    hdr['network'] = 'KNET'
    hdr['station'] = lines[5].split()[2]
    standard['station_name'] = ''

    # according to the powers that defined the Network.Station.Channel.Location
    # "standard", Location is a two character field.  Most data providers,
    # including KNET here, don't provide this.  We'll flag it as "--".
    hdr['location'] = '--' 
    
    coordinates['latitude'] = float(lines[6].split()[2])
    coordinates['longitude'] = float(lines[7].split()[2])
    coordinates['elevation'] = float(lines[8].split()[2])
    
    hdr['sampling_rate'] = float(
        re.search('\\d+', lines[10].split()[2]).group())
    hdr['delta'] = 1 / hdr['sampling_rate']
    hdr['calib'] = 1.0
    standard['units'] = 'acc'

    
    if lines[12].split()[1] == 'N-S':
        hdr['channel'] = 'H1'
    elif lines[12].split()[1] == 'E-W':
        hdr['channel'] = 'H2'
    elif lines[12].split()[1] == 'U-D':
        hdr['channel'] = 'Z'
    else:
        raise Exception('Could not parse direction %s' %
                        lines[12].split()[1])

    scalestr = lines[13].split()[2]
    parts = scalestr.split('/')
    num = float(parts[0].replace('(gal)', ''))
    den = float(parts[1])
    calib = num / den

    duration = float(lines[11].split()[2])

    hdr['npts'] = int(duration * hdr['sampling_rate'])

    timestr = ' '.join(lines[9].split()[2:4])
    hdr['starttime'] = datetime.strptime(timestr, TIMEFMT)

    # read in the data - there is a max of 8 columns per line
    # the code below handles the case when last line has
    # less than 8 columns
    if hdr['npts'] % COLS_PER_LINE != 0:
        nrows = int(np.floor(hdr['npts'] / COLS_PER_LINE))
        nrows2 = 1
    else:
        nrows = int(np.ceil(hdr['npts'] / COLS_PER_LINE))
        nrows2 = 0
    data = np.genfromtxt(filename, skip_header=TEXT_HDR_ROWS,
                         max_rows=nrows, filling_values=np.nan)
    data = data.flatten()
    if nrows2:
        skip_header = TEXT_HDR_ROWS + nrows
        data2 = np.genfromtxt(filename, skip_header=skip_header,
                              max_rows=nrows2, filling_values=np.nan)
        data = np.hstack((data, data2))
        nrows += nrows2

    # apply the correction factor we're given in the header
    data *= calib

    # fill out the rest of the standard dictionary
    standard['horizontal_orientation'] = np.nan
    standard['instrument_period'] = np.nan
    standard['instrument_damping'] = np.nan
    standard['processing_time'] = ''
    standard['process_level'] = 'V1'
    standard['sensor_serial_number'] = ''
    standard['instrument'] = ''
    standard['comments'] = ''
    standard['structure_type'] = ''
    standard['corner_frequency'] = np.nan
    standard['units'] = 'acc'
    standard['source'] = 'Japan National Research Institute for Earth Science and Disaster Resilience'
    standard['source_format'] = 'KNET'

    hdr['coordinates'] = coordinates
    hdr['standard'] = standard

    # create a Trace from the data and metadata
    trace = Trace(data.copy(), Stats(hdr.copy()))

    # to match the max values in the headers,
    # we need to detrend/demean the data (??)
    trace.detrend('linear')
    trace.detrend('demean')

    stream = Stream(trace)
    return stream
