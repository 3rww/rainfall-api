'''
api.py

A lightweight Flask application that provides a clean API for the legacy 3RWW
rainfall data (rain gauge and gauage-adjusted radar rainfall data).

'''

# standard library
import os
# from collections import OrderedDict
# framework
from flask import Flask, render_template, redirect, url_for
# API
from flask_restful import Resource, Api, reqparse, inputs
from flasgger import Swagger, swag_from
# web requests
import requests
# date/time parsing
from datetime import datetime, timedelta
from dateutil.parser import parse
from dateutil import tz
# HTML parsing
import bs4
from bs4 import BeautifulSoup
# data transformation
import petl as etl
from sortedcontainers import SortedDict
# geojson spec
# from geojson import Point, Feature, FeatureCollection
import json

# ----------------------------------#
# FLASK APP
application = Flask(__name__)
application.debug = True

application.config['URL_GAGE'] = "http://web.3riverswetweather.org/trp:API.raingauge"
application.config['URL_GARR'] = "http://web.3riverswetweather.org/trp:API.pixel"

# ReST-ful API via Flask-Restful
api = Api(application)

# Swagger API docs
application.config['SWAGGER'] = {
    'title': '3RWW Rainfall API (beta)',
    'uiversion': 3
}
swag = Swagger(
    application,
    template={
        "info": {
            "title": "3RWW Rainfall API (beta)",
            "description": "API for rainfall data collected and maintained by 3 Rivers Wet Weather",
            "contact": {
                "responsibleOrganization": "CivicMapper",
                "responsibleDeveloper": "Christian Gass",
                "email": "christian.gass@civicmapper.com",
                "url": "http://www.3riverswetweather.org/municipalities/calibrated-radar-rainfall-data",
            },
            "version": "0.1.0"
        },
        #   "host": "mysite.com",  # overrides localhost:5000
        # "basePath": "/api",  # base bash for blueprint registration
        "schemes": [
            "http",
            "https"
        ]
    }
)

# ----------------------------------------------------------------------------
# HELPERS

# preload a list of pixel IDs from a file (utilized by the GARR endpoint)
pixel_csv = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "data", "grid_centroids.csv")
basin_lookup_file = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "data", "lookup_basins.json")
all_pixels = list(etl.fromcsv(pixel_csv).values('id'))
with open(basin_lookup_file, mode='r') as fp:
    basin_pixels = json.load(fp)


def handle_utc(datestring, direction="to_local", local_zone='America/New_York'):
    """ parse from a date/time string
    """

    # METHOD 1: Hardcode zones:
    from_zone = tz.gettz('UTC')
    to_zone = tz.gettz(local_zone)

    # METHOD 2: Auto-detect zones:
    # from_zone = tz.tzutc()
    # to_zone = tz.tzlocal()

    # parse the ISO 8601-formatted, UTC (zulu) string into a datetime object.
    # e.g., '2017-03-03T17:00:00Z'
    t = parse(datestring)

    if direction == "to_local" or direction == "from_utc":
        # Tell the datetime object that it's in UTC time zone since
        # datetime objects are 'naive' by default
        t = t.replace(tzinfo=from_zone)

        # Convert time zone
        tc = t.astimezone(to_zone)

        # return result as ISO 8601-formatted string, now with UTC offset
        # e.g., '2017-03-03T12:00:00-05:00'
        # return tc.isoformat()
        return tc

    elif direction == "to_utc" or direction == "from_local":

        t = t.replace(tzinfo=to_zone)

        # Convert time zone
        tc = t.astimezone(from_zone)

        # return tc.isoformat()
        return tc

    else:
        raise Exception
        # print("incorrect datetime conversion direction string (must be 'to_utc' or 'to_local')")


def datetime_last24hours():
    '''return start and ending date-time ISO strings, where the end time is
    exactly now, and the start time is exactly 24 hours ago.
    '''
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    start = handle_utc(yesterday.isoformat())
    end = handle_utc(now.isoformat())
    return start, end


def parse_pixels(list_of_ids):
    '''given list of 6-digit pixel ids, form the expected format of the API
    pixel argument
    '''
    return ";".join(["{0},{1}".format(i[:3], i[3:]) for i in list_of_ids])


def parse_gauge_ids(list_of_ids):
    """give list of rain gauge ids, form the expected format of the API gauge
    ID argument
    """
    return ",".join([i for i in list_of_ids])


def reverse_parse_pixels(pixels_arg):
    '''turns semi-colon and comma-delimited pixels arg into a list of 6 digit pixel ids
    '''
    return ["{0}{1}".format(s.split(",")[0], s.split(",")[1]) for s in pixels_arg.split(";")]


def reverse_parse_pixels_xy(pixels_arg):
    '''turns semi-colon and comma-delimited pixels arg into a dictionary of
    its arbitrary x,y coordinates
    '''
    return [{"x": s.split(",")[0], "y":s.split(",")[1]} for s in pixels_arg.split(";")]


def parse_response_html(page):
    '''
    Takes the HTML page returned by the 3RWW Rainfall site and turns it into
    structured data. Returns a Python PETL table object.
    '''
    t1 = []
    soup = BeautifulSoup(page.text, 'html.parser')
    # this gets the header elements as strings in a list
    # hrow = soup.table.tr.find_all_next("th")
    th = soup.table.find_next("tr")
    realheader = [
        x.contents[0].replace(',', '') for x in th.children
        if isinstance(x, bs4.element.Tag)
    ]
    t1.append(realheader)
    # this gets each row as a string in a list:
    trs = soup.table.tr.find_next_siblings("tr")
    # iterate through those to get the values out
    for tr in trs:
        realrow = [
            # get the contents and strip the whitespace
            each.contents[0].lstrip().rstrip()
            # we only want the rows that are tags and not tagged "center"
            for each in tr
            if (
                isinstance(each, bs4.element.Tag)
                and
                each.findChild("center") is None
            )
        ]
        # timestamp = realrow[0]
        # datapoints = realrow[1:]
        t1.append(realrow)

    # use petl to give us the flexibility to return multiple types/structures
    d = etl.dicts(t1)
    t2 = etl.fromdicts(d, header=realheader)
    return t2


def transform_teragon_csv(teragon_csv, transpose=False, indexed=False):
    """transform Teragon's CSV response into a python dictionary,
    which mirrors the JSON response we want to provide to API clients

    Arguments:
        teragon_csv {reference} -- reference to a CSV table on disk
        or in memory
        transpose {boolean} -- transpose Teragon table
        indexed {boolean} -- return dictionary in indexed format or as records

    Returns:
        {dict} -- a dictionary representing the Terragon table, transformed
        for ease of use in spatial/temporal data vizualation
    """

    petl_table = etl.fromcsv(teragon_csv)
    # print(petl_table)
    # get iterable of column pairs (minus 'Timestamp')
    # this is used to group the double columns representing a single
    # data point in Teragon's CSV

    h = list(etl.header(petl_table))
    xy_cols = zip(* [iter(h[1:])] * 2)

    # make a new header row
    new_header = ['Timestamp']
    fields_to_cut = []
    for each in xy_cols:
        # print(each)
        # correct id, assembled from columns
        id_col, note_col = each[0], each[1]
        # assemble new id column, to replace of PX column (which has data)
        # id_col = "{0}{1}".format(px[:3], px[4:])
        # assemble new notes column, to replace of PY column (which has notes)
        notes_col = "{0}-n".format(id_col)
        # add those to our new header (array)
        new_header.extend([id_col, notes_col])
        # track fields that we might want to remove
        fields_to_cut.append(notes_col)

    # transform the table
    table = etl \
        .setheader(petl_table, new_header) \
        .cutout(*tuple(fields_to_cut))  \
        .select('Timestamp', lambda v: v.upper() != 'TOTAL')  \
        .convert('Timestamp', lambda t: parse(t).isoformat())  \
        .replaceall('N/D', None)

    # transpose the table, so that rows are cells/gauges and columns are times
    # (note that this operation can take a while)
    if transpose:
        table = etl.transpose(table)

    # if indexed: format data where cells/gauges or times are keys, and
    # rainfall amounts are values
    # otherwise, format as nested records (arrays of dicts)

    if indexed:
        data = SortedDict()
        for row in etl.dicts(table):
            inside = SortedDict()
            for d in row.items():
                if d[0] != 'Timestamp':
                    if d[1]:
                        v = float(d[1])
                    else:
                        v = d[1]
                    inside[d[0]] = v
            data[row['Timestamp']] = inside
        return data

    else:
        rows = []
        # create a nested dictionary from the table
        for row in etl.dicts(table):
            data = []
            for d in row.items():
                if d[0] != 'Timestamp':
                    if d[1]:
                        v = float(d[1])
                    else:
                        v = d[1]
                    data.append({
                        'id': d[0],
                        'v': v
                    })
            rows.append({
                "id": row['Timestamp'],
                "d": data
            })
        # print(rows)
        # print(json.dumps(rows, indent=2))
        return rows


def parse_common_teragon(args):
    """handles parsing and defaults for common Teragon API
    arguments (everything except gauge IDs or GARR pixel IDs)

    Arguments:
        args {obj} -- Flask-Restful args parser object

    Returns:
        dict -- mostly complete payload for the Teragon API
    """

    # handle the dates; default to past 24 hours if no args
    if args['dates']:
        parsed_dates = inputs.iso8601interval(args['dates'])
        if parsed_dates:
            start, end = parsed_dates
        else:
            start, end = datetime_last24hours()
    else:
        start, end = datetime_last24hours()

    # handle the interval, default to hourly if no args
    if args['interval'] not in ["Daily", "Hourly", "15-minute"]:
        interval = "Hourly"
    else:
        interval = args['interval']

    # transform zerofill, default to off if no args
    if args['zerofill'] == True:
        zerofill = 'yes'
    else:
        zerofill = ''

    return {
        "startmonth": start.month,
        "startday": start.day,
        "startyear": start.year,
        "starthour": start.hour,
        "endmonth": end.month,
        "endday": end.day,
        "endyear": end.year,
        "endhour": end.hour,
        "interval": interval,
        "zerofill": zerofill,
    }


def etl_data_from_teragon(url, data, tranpose, indexed):
    """handles making request to the teragon service and transform the response

    Arguments:
        url {str} -- Teragon API endpoint
        data {dict} -- request payload (always sent as data via POST)
        tranpose {bool} --  transpose the resulting table (default: False)

    Returns:
        {dict} -- Teragon API response transformed into a nested dictionary, ready to be transmitted as JSON
    """

    response = requests.post(url, data=data)
    # print(response.request.body)
    # post-process and return the response
    table = etl.MemorySource(response.text.encode())
    return transform_teragon_csv(table, tranpose, indexed)

# ----------------------------------------------------------------------------
# REST API Arguments
# define parsers/validation for all types of request params
# NOTE: the validation functionality of reqparse somewhat interferes with
# Flasgger's validation; we also have some simple default reversion built
# into the request functions. We'll likely clean most of this up in the future.


parser = reqparse.RequestParser()

parser.add_argument(
    'ids', type=str, help='List of rain gauge IDs or GARR pixels', required=False)
parser.add_argument(
    'basin',
    type=str,
    choices=["all basins", "Chartiers Creek", "Lower Ohio + Girty's Run", "Main Rivers",
             "Saw Mill Run", "Turtle Thompson", "Upper Allegheny Pine Creek", "Upper Monongahela", "", None],
    help='Basin for which to get rainfall data. This is effectively a shortcut for the ids parameter. Defaults to all basins. If ids are specified in the ids parameter, this parameter will be ignored.',
    required=False)
parser.add_argument(
    'dates',
    type=str,
    help='Date-time(s) in ISO 8601 datetime format. A single date-time returns just that date. An interva lISO 8601 datetime range (e.g.: "2016-08-28T14:00/2016-08-29T06:00") returns all data in between.',
    required=False
)
parser.add_argument(
    'interval',
    type=str,
    help='Interval of rainfall data: "Daily", "Hourly", "15-minute". Defaults to "Hourly"',
    choices=["Daily", "Hourly", "15-minute", "", None],
    default="Hourly",
    required=False
)
parser.add_argument(
    'zerofill',
    type=str,
    help='Include data points with zero values.',
    # choices=["True", "False", "", True, False, None],
    # default=False,
    required=False
)
parser.add_argument(
    'keyed_by',
    type=str,
    help='determines how data is transformed: "time" or "location"',
    choices=["time", "location", "", None],
    default="time",
    required=False
)
parser.add_argument(
    'geom',
    type=str,
    help='The geometry type of the garr grid: "polygon" returns the grid; "point" returns the centroids of the grid cells.',
    choices=["point", "polygon"],
    default="polygon",
    required=False
)

# ----------------------------------#
# REST API Resources


class Gage(Resource):
    @swag_from('apidocs/apidocs-gage-get.yaml')
    def get(self):

        # get the request args
        args = parser.parse_args()
        # print(args)

        # assemble the payload
        payload = parse_common_teragon(args)

        # handle the ids parameter; default to all if not provided
        if not args['ids']:
            ids = [x for x in range(1, 34)]
        else:
            ids = parse_gauge_ids(args['ids'].split(","))
        # print(ids)
        payload['gauges'] = ids

        # handle the keyed_by parameter
        if not args['keyed_by'] or (args['keyed_by'] not in ["time", "location"]):
            # default is data keyed by time, same as Teragon API
            tranpose = False
        else:
            if args['keyed_by'] == "time":
                tranpose = False
            elif args['keyed_by'] == "location":
                # alternatively, we transpose the data so it's keyed by location
                tranpose = True
            else:
                # default is data keyed by time, same as Teragon API
                tranpose = False

        print(payload)

        # make the request and return the response
        return etl_data_from_teragon(
            application.config['URL_GAGE'],
            data=payload,
            tranpose=tranpose,
            indexed=True
        )


class Garr(Resource):
    @swag_from('apidocs/apidocs-garr-post.yaml')
    def post(self):

        # get the request args
        args = parser.parse_args()
        # print(args)

        # assemble the payload
        payload = parse_common_teragon(args)

        # handle the pixels or basin parameters
        # if pixels not provided
        if not args['ids']:
            # if basin not provided
            if not args['basin']:
                # use all pixels
                pixels = ";".join(all_pixels)
            else:
                # if basin is provided but pixels not, use basin
                pixels = parse_pixels(basin_pixels[args['basin']])
        else:
            # use all pixels
            pixels = parse_pixels(args['ids'].split(","))
        payload['pixels'] = pixels
        print(payload)

        # handle the keyed_by parameter
        if not args['keyed_by'] or (args['keyed_by'] not in ["time", "location"]):
            # default is data keyed by time, same as Teragon API
            tranpose = False
        else:
            if args['keyed_by'] == "time":
                tranpose = False
            elif args['keyed_by'] == "location":
                # alternatively, we transpose the data so it's keyed by location
                tranpose = True
            else:
                # default is data keyed by time, same as Teragon API
                tranpose = False

        # make the request and return the response
        return etl_data_from_teragon(
            application.config['URL_GARR'],
            data=payload,
            tranpose=tranpose,
            indexed=True
        )


class Grid(Resource):
    @swag_from('apidocs/apidocs-garrgrid-get.yaml')
    def get(self):

        # get the request args
        args = parser.parse_args()
        # print(args)

        # handle the geom argument; default to polygon
        if args['geom'] not in ["point", "polygon"]:
            shape = "polygon"
        else:
            shape = args['geom']

        # based on argument, get correct file
        if shape == "polygon":
            pixel_json_file_name = "grid.geojson"
        elif shape == "point":
            pixel_json_file_name = "grid_centroids.geojson"

        # load geojson reference file from disk and return it as python dict
        pixel_json_file_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "data", pixel_json_file_name)
        with open(pixel_json_file_path) as f:
            pixel_json = json.load(f)
            return pixel_json

        return None


# ----------------------------------------------------------------------------
# ROUTES

@application.route('/', methods=['GET'])
def home():
    return redirect('/apidocs/', code=302)


api.add_resource(Garr, '/api/garrd/')
api.add_resource(Gage, '/api/gauge/')
api.add_resource(Grid, '/api/garrd/grid')

if __name__ == "__main__":
    application.run()
