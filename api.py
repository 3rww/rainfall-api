'''
api.py

A lightweight Flask application that provides a clean API for the legacy 3RWW 
rainfall data (rain gauge and gauage-adjusted radar rainfall data).

'''

# standard library
from collections import OrderedDict
import os
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
# geojson spec
# from geojson import Point, Feature, FeatureCollection
import json

# ----------------------------------#
# FLASK APP
app = Flask(__name__)
app.debug = True
app.config['USERID'] = 'guest'
app.config['PASSWD'] = 'guest'
app.config['USERPTR'] = '00000000/00000000/00000000/01010002/54550802/44010828/01110084/AA9A71A2'
app.config['URL_GAGE'] = "http://web.3riverswetweather.org/trp:Main.hist2_html;trp:,,/data"
app.config['URL_GARR'] = "http://web.3riverswetweather.org/trp:Region.show_pixel_data_html;trp:,,/data"

# ReST-ful API via Flask-Restful
api = Api(app)

# Swagger API docs
app.config['SWAGGER'] = {
    'title': '3RWW Rainfall API (beta)',
    'uiversion': 2
}
swag = Swagger(
    app, 
    template={
        "swagger": "2.0",
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
pixel_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)),"data","grid_centroids.csv")
all_pixels = list(etl.fromcsv(pixel_csv).values('id'))

def handle_utc(datestring, direction="to_local", local_zone='America/New_York'):
    """ parse from a date/time string
    """
    
    # METHOD 1: Hardcode zones:
    from_zone = tz.gettz('UTC')
    to_zone = tz.gettz(local_zone)

    # METHOD 2: Auto-detect zones:
    #from_zone = tz.tzutc()
    #to_zone = tz.tzlocal()
    
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
        #return tc.isoformat()
        return tc

    elif direction == "to_utc" or direction == "from_local":
        
        t = t.replace(tzinfo=to_zone)
        
        # Convert time zone
        tc = t.astimezone(from_zone)
        
        #return tc.isoformat()
        return tc
    
    else:
        raise Exception
        #print("incorrect datetime conversion direction string (must be 'to_utc' or 'to_local')")

def parse_pixels(list_of_ids):
    '''given list of 6-digit pixel ids, form the expected format of the API 
    pixel argument
    '''
    return ";".join(["{0},{1}".format(i[:3],i[3:]) for i in list_of_ids])

def reverse_parse_pixels(pixels_arg):
    '''turns semi-colon and comma-delimited pixels arg into a list of 6 digit pixel ids
    '''
    return ["{0}{1}".format(s.split(",")[0],s.split(",")[1]) for s in pixels_arg.split(";")]

def reverse_parse_pixels_xy(pixels_arg):
    '''turns semi-colon and comma-delimited pixels arg into a dictionary of 
    its arbitrary x,y coordinates
    '''
    return [{"x":s.split(",")[0],"y":s.split(",")[1]} for s in pixels_arg.split(";")]

def parse_response_html(response):
    '''
    Takes the HTML page returned by the 3RWW Rainfall site and turns it into 
    structured data. Returns a Python PETL table object.
    '''
    t1 = []
    soup = BeautifulSoup(response.text, 'html.parser')
    # this gets the header elements as strings in a list
    #hrow = soup.table.tr.find_all_next("th")
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
        #timestamp = realrow[0]
        #datapoints = realrow[1:]
        t1.append(realrow)

    # use petl to give us the flexibility to return multiple types/structures
    d = etl.dicts(t1)
    t2 = etl.fromdicts(d, header=realheader)
    return t2

def return_json(petl_table, method):
    '''
    takes a petl table and transform it into an ordered dictionary, 
    keyed by either time or the ID of the thing (rain gauge or GARR pixel)

    Used for post-processing the results from the call to 3RWW's legacy rainfall
    data system.

    response_by_location = {
        "id1" : [
            {"time1": "value"},
            {"time2": "value"}
        ],
        "id2" : [
            {"time1": "value"},
            {"time2": "value"}
        ]
    }
    response_by_time = {
        "time1" : [
            {"id1": "value"},
            {"id2": "value"}
        ],
        "time2" : [
            {"id1": "value"},
            {"id2": "value"}
        ]
    }
    '''
    print(method)

    if method not in ["time","location"]:
        m = "location"
    else:
        m = method

    if m == "time":
        print("by time")
        records = list(etl.dicts(petl_table))
    elif m == "location" or method is None:
        print("by location")
        records = list(etl.dicts(etl.transpose(petl_table)))

    d = {}
    for rec in records:
        if 'Timestamp' in rec.keys():
            n = rec.pop('Timestamp')
        elif 'TOTAL:' in rec.keys():
            n = rec.pop('TOTAL:')
        else:
            n = 'null'
        d[n] = rec
    d2 = OrderedDict(sorted(d.items(), key=lambda t: t[0]))

    return d2

def datetime_last24hours():
    '''return start and ending date-time ISO strings, where the end time is
    exactly now, and the start time is exactly 24 hours ago.
    '''
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    start = handle_utc(yesterday.isoformat())
    end = handle_utc(now.isoformat())
    return start, end


# ----------------------------------------------------------------------------
# REST API Arguments
# define parsers/validation for all types of request params
# NOTE: the validation functionality of reqparse somewhat interferes with 
# Flasgger's validation; we also have some simple default reversion built 
# into the request functions. We'll likely clean most of this up in the future.

parser = reqparse.RequestParser()

parser.add_argument('ids', type=str, help='List of rain gauge IDs, e.g.:1,2,3',required=False)
parser.add_argument('pixels',type=str, help='List of pixel IDs, e.g.:1,2,3',required=False)
parser.add_argument(
    'dates', 
    type=str, 
    help='Date-time(s) in iso8601 format(s). A single date-time returns just that date. An interval (e.g.: "2016-08-28T14:00/2016-08-29T06:00") returns all data in between.',
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
    help='Include data points with zero values',
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
    @swag_from('docs/apidocs-gage-get.yaml')
    def get(self):
        '''
        http://localhost:5000/gauges/?ids=[10,11,12,13,14,15]&dates=2016-08-28T20:00/2016-08-29T06:00&interval=Hourly

        defaults:
            dates: past 24 hours
            ids: all ids (all gauges)
            keyed_by: time

        '''

        # get the request args
        args = parser.parse_args()
        print(args)

        # handle the ids; default to all if not provided
        if not args['ids']:
            ids = [x for x in range(1, 34)]
        else:
            ids = args['ids'].split(",")

        # handle the dates; default to past 24 hours if no args
        if args['dates']:
            parsed_dates = inputs.iso8601interval(args['dates'])
            if parsed_dates: 
                start, end = parsed_dates
            else:
                start, end = datetime_last24hours()
        else:
            start, end = datetime_last24hours()

        if args['keyed_by'] not in ["time", "location"]:
            keyed_by = "time"
        else:
            keyed_by = args['keyed_by']

        # handle the interval, default to hourly if no args
        if args['interval'] not in ["Daily", "Hourly", "15-minute"]:
            interval = "Hourly"
        else:
            interval = args['interval']

        # transform zerofill, default to off if no args
        if args['zerofill'] == True:
            zerofill = 'on'
        else:
            zerofill = 'off'

        # assemble the payload
        payload = {
            "_userid":app.config['USERID'],
            "_passwd":app.config['PASSWD'],
            "_userptr":app.config['USERPTR'],
            "startmonth":start.month,
            "startday":start.day,
            "startyear":start.year,
            "starthour":start.hour,
            "targetday":"{0}/{1}/{2}".format(start.year,start.month,start.day), #str: "2017/3/1" , #str: "2017/3/1" 
            "endmonth":end.month,
            "endday":end.day,
            "endyear":end.year,
            "endhour":end.hour,
            "targetday2":"{0}/{1}/{2}".format(end.year,end.month,end.day), #str: "2017/3/1" , #str: "2017/3/30"
            "interval": interval, #str: "Daily", "Hourly" "15-minute"
            "zerofill": zerofill, #str: "on", "off"
            # "view.x":viewx, #???? 26
            # "view.y":viewy, #???? 5
        }
        
        # build gauge params from list of gauge numbers
        print(ids)
        for each in ids:
            gauge_param = "g{0}".format(each)
            payload[gauge_param] = 'on'
        print(payload)

        r = requests.post(app.config['URL_GAGE'], data=payload)

        data = parse_response_html(r)

        return return_json(data, keyed_by)


class Garr(Resource):
    @swag_from('docs/apidocs-garr-get.yaml')
    def get(self):
        '''
        http://localhost:5000/garr/?pixels=147125,148125,149125,150125,147126,148126,149126,150126,147127,148127,149127,150127,147128,148128,149128,150128&dates=2016-08-28T20:00/2016-08-29T06:00&interval=Hourly&zerofill=on
        '''

        # get the request args
        args = parser.parse_args()
        print(args)

        # handle the pixels; default to all if not provided
        if not args['pixels']:
            pixels = ";".join(all_pixels)
        else:
            pixels = parse_pixels(args['pixels'].split(","))

        # handle the dates; default to past 24 hours if no args
        if args['dates']:
            parsed_dates = inputs.iso8601interval(args['dates'])
            if parsed_dates: 
                start, end = parsed_dates
            else:
                start, end = datetime_last24hours()
        else:
            start, end = datetime_last24hours()


        if args['keyed_by'] not in ["time", "location"]:
            keyed_by = "time"
        else:
            keyed_by = args['keyed_by']

        # handle the interval, default to hourly if no args
        if args['interval'] not in ["Daily", "Hourly", "15-minute"]:
            interval = "Hourly"
        else:
            interval = args['interval']

        # transform zerofill, default to off if no args
        if (args['zerofill'] in ['True','true']) or (args['zerofill'] == True):
            zerofill = 'on'
        else:
            zerofill = 'off'
        
        payload = {
            "pixels":pixels,
            "_userid":app.config['USERID'],
            "_passwd":app.config['PASSWD'],
            "_userptr":app.config['USERPTR'],
            "startmonth":start.month,
            "startday":start.day,
            "startyear":start.year,
            "starthour":start.hour,
            "targetday":"{0}/{1}/{2}".format(start.year,start.month,start.day), #str: "2017/3/1" , #str: "2017/3/1" 
            "endmonth":end.month,
            "endday":end.day,
            "endyear":end.year,
            "endhour":end.hour,
            "targetday2":"{0}/{1}/{2}".format(end.year,end.month,end.day), #str: "2017/3/1" , #str: "2017/3/30"
            "interval": interval, #str: "Daily", "Hourly" "15-minute"
            "zerofill": zerofill, #str: "on", "off"
            # "view.x":viewx, #???? 26
            # "view.y":viewy, #???? 5
        }
        print(payload)
        
        r = requests.post(app.config['URL_GARR'], data=payload)
        print(r.text)

        data = parse_response_html(r)

        return return_json(data, keyed_by)


class Grid(Resource):
    @swag_from('docs/apidocs-garrgrid-get.yaml')
    def get(self):
        '''
        http://localhost:5000/garr/grid
        '''

        # get the request args
        args = parser.parse_args()
        print(args)        

        # handle the geom argument; default to polygon
        if args['geom'] not in ["point","polygon"]:
            shape = "polygon"
        else:
            shape = args['geom']

        # based on argument, get correct file
        if shape == "polygon":
            pixel_json_file_name = "grid.geojson"
        elif shape == "point":
            pixel_json_file_name = "grid_centroids.geojson"

        # OLD - load pixel data from csv, convert to GeoJSON-compliant python dict
        # pixel_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "grid.csv")
        # pixels = etl.fromcsv(pixel_csv).dicts()
        # features = []
        # features.append(Feature(geometry=Point((float(each['X']),float(each['Y']))), properties={"id": each['PIXEL']}))
        # return FeatureCollection(features)

        # load geojson reference file from disk and return it as python dict
        pixel_json_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", pixel_json_file_name)
        with open(pixel_json_file_path) as f:
            pixel_json = json.load(f)
            return pixel_json

        return None


# ----------------------------------------------------------------------------
# ROUTES

@app.route('/', methods=['GET'])
def home():
    return redirect('/apidocs/',code=302)

api.add_resource(Garr, '/garrd/')
api.add_resource(Gage, '/gauge/')
api.add_resource(Grid, '/garrd/grid')

if __name__ == "__main__":
    app.run()