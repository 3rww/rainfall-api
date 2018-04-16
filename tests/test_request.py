'''
curl -d zerofill=0 -d interval=Daily -d "pixels=135,142;135,143;135,144" -d startyear=2000 -d startmonth=4 -d startday=1 -d starthour=0 -d endyear=2010 -d endmonth=1 -d endday=1 -d endhour=0 http://web.3riverswetweather.org/trp:API.pixel
'''

import random
from datetime import datetime
from dateutil.parser import parse
import requests
import petl as etl
import timeit
from collections import OrderedDict
import json


def transform_teragon_csv(teragon_csv):
    """transform Teragon's CSV response into a python dictionary,
    which mirrors the JSON response we want to provide to API clients

    Arguments:
        teragon_csv {reference} -- reference to a CSV table on disk 
        or in memory

    Returns:
        {dict} -- a dictionary representing the Terragon table, transformed
        for ease of use in spatial/temporal data vizualation
    """

    petl_table = etl.fromcsv(teragon_csv)
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
        px, py = each[0], each[1]
        # assemble new id column, to replace of PX column (which has data)
        # id_col = "{0}{1}".format(px[3:], px[4:])
        # assemble new notes column, to replace of PY column (which has notes)
        notes_col = "{0}-n".format(px)
        # add those to our new header (array)
        new_header.extend([px, notes_col])
        # track fields that we might want to remove
        fields_to_cut.append(notes_col)

    # transform the table
    table = etl \
        .setheader(petl_table, new_header) \
        .cutout(*tuple(fields_to_cut))  \
        .select('Timestamp', lambda v: v.upper() != 'TOTAL') \
        .convert('Timestamp', lambda t: parse(t).isoformat()) \
        .replaceall('N/D', None) \
        .dicts()

    rows = []
    for row in table:
        data = []
        for d in row.items():
            if d[0] != 'Timestamp':
                data.append({
                    'id': d[0],
                    'v': d[1]
                })
        rows.append({
            "id": row['Timestamp'],
            "d": data
        })
    # print(rows)
    # print(json.dumps(rows, indent=2))
    return {"garr": rows}


cells = etl.fromcsv(r"C:\GitHub\3rww\rainfall\api\data\grid.csv")
list_of_ids = [row['PIXEL'] for row in etl.dicts(cells)]
# list_of_ids = random.sample(list_of_ids, 500)
# short_list = ['173113', '169130', '147129', '163152', '147142']
# print(short_list)
pixels = ";".join(["{0},{1}".format(i[:3], i[3:]) for i in list_of_ids])

start = datetime(2004, 9, 17, 3)
end = datetime(2004, 9, 18, 0)
# start = datetime(2004, 9, 17, 11)
# end = datetime(2004, 9, 17, 13)
interval = ""
zerofill = ""

payload = {
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
    "pixels": pixels
}
# print(payload)

url = 'http://web.3riverswetweather.org/trp:API.pixel'

# request data from Teragon API
start_time = timeit.default_timer()
response = requests.get(url, data=payload)
elapsed = timeit.default_timer() - start_time
print("response received in {0} seconds".format(elapsed))

# read and transform Teragon response
start_time = timeit.default_timer()
# convert text of response to bytes and read through memory
source = etl.MemorySource(response.text.encode())
# read from memory as if it were a csv file on disk
# table1 = etl.fromcsv(source)
# transform table into dict, ready for conversion to structured JSON
table1_json = transform_teragon_csv(source)
# print(json.dumps(table1_json, indent=2))
print("processing table took {0} seconds".format(
    timeit.default_timer() - start_time))

# Transpose (can take a really long time with PETL!)
# start_time = timeit.default_timer()
# table2 = etl.transpose(t2_rows)
# print("{0} rows after transpose eval".format(etl.nrows(table2)))
# print("transposing took {0} seconds".format(
#     timeit.default_timer() - start_time))
# # print(etl.lookall(table2, style='minimal'))
