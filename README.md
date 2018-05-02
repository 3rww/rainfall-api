# 3RWW Rainfall API

> A clean API for 3RWW's immense trove of historical rainfall data for Allegheny County

This API taps the existing legacy 3RWW rainfall website for data, and returns it back in a temporally- or spatially-indexed, structured `json` format. It is designed to enable straightforward, programmatic access to 3RWW's amazing trove of historical rainfall data for Allegheny County, and in turn will support the planned development of new graphic user interface(s) for that data.

This application should be considered an alpha product: it's likely that the structure of the requests, endpoints, and other things will change; error handling is rudimentary and useful error messages are basically non-existent; the API documentation is not complete.

# Usage

Head to `http://3rww-rainfall-api.civicmapper.com/apidocs/` to explore the endpoints and documentation in an interactive Swagger UI.

# Stack

Built with [Python-Flask](http://flask.pocoo.org/) and [Flasgger](https://github.com/rochacbruno/flasgger), among other things.

# Development & Deployment

(to be completed)
