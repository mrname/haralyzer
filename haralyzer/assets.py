"""
Provides all of the main functional classes for analyzing HAR files
"""

import functools
import datetime
import re

from collections import Counter
from cached_property import cached_property

# I know this import is stupid, but I cannot use dateutil.parser without it
from dateutil import parser

from .compat import iteritems
from .errors import PageNotFoundError
from .http import Request, Response
from .mixins import MimicDict

DECIMAL_PRECISION = 0


def convert_to_entry(func):
    @functools.wraps(func)
    def inner(*args, **kwargs):
        # Changed to list because tuple does not support item assignment
        changed_args = list(args)
        # Convert the dict (first argument) to HarEntry
        if isinstance(changed_args[0], dict):
            changed_args[0] = HarEntry(changed_args[0])
        # For some cases have HarParser as the first type with the Entry and second
        if isinstance(changed_args[0], HarParser):
            changed_args[1] = HarEntry(changed_args[1])
        return func(*tuple(changed_args), **kwargs)
    return inner


class HarParser(object):
    """
    A Basic HAR parser that also adds helpful stuff for analyzing the
    performance of a web page.
    """

    def __init__(self, har_data=None):
        """
        :param har_data: a ``dict`` representing the JSON of a HAR file
        (i.e. - you need to load the HAR data into a string using json.loads or
        requests.json() if you are pulling the data via HTTP.
        """
        if not har_data or not isinstance(har_data, dict):
            raise ValueError(
                'A dict() representation of a HAR file is required'
                ' to instantiate this class. Please RTFM.')
        self.har_data = har_data['log']

    @convert_to_entry
    def match_headers(self, entry, header_type, header, value, regex=True):
        """
        Function to match headers.

        Since the output of headers might use different case, like:

            'content-type' vs 'Content-Type'

        This function is case-insensitive

        :param entry: ``HarEntry`` object to analyze
        :param header_type: ``str`` of header type. Valid values:

            * 'request'
            * 'response'

        :param header: ``str`` of the header to search for
        :param value: ``str`` of value to search for
        :param regex: ``bool`` indicating whether to use regex or exact match

        :returns: a ``bool`` indicating whether a match was found
        """
        if header_type not in ["request", "response"]:
            raise ValueError('Invalid header_type, should be either:\n\n'
                             '* \'request\'\n*\'response\'')

        # TODO - headers are empty in some HAR data.... need fallbacks here
        for h in getattr(entry, header_type).headers:
            if h['name'].lower() == header.lower() and h['value'] is not None:
                if regex and re.search(value, h['value'], flags=re.IGNORECASE):
                    return True
                elif value == h['value']:
                    return True
        return False

    @staticmethod
    @convert_to_entry
    def match_content_type(entry, content_type, regex=True):
        """
        Matches the content type of a request using the mimeType metadata.

        :param entry: ``HarEntry`` object to analyze
        :param content_type: ``str`` of regex to use for finding content type
        :param regex: ``bool`` indicating whether to use regex or exact match.
        """
        mime_type = entry.response.mimeType

        if regex and re.search(content_type, mime_type, flags=re.IGNORECASE):
            return True
        elif content_type == mime_type:
            return True

        return False

    @convert_to_entry
    def match_request_type(self, entry, request_type, regex=True):
        """
        Helper function that returns entries with a request type
        matching the given `request_type` argument.

        :param entry: ``HarEntry`` object to analyze
        :param request_type: ``str`` of request type to match
        :param regex: ``bool`` indicating whether to use a regex or string match
        """
        if regex:
            return re.search(request_type, entry.request.method,
                             flags=re.IGNORECASE) is not None
        else:
            return entry.request.method == request_type

    @staticmethod
    @convert_to_entry
    def match_http_version(entry, http_version, regex=True):
        """
        Helper function that returns entries with a request type
        matching the given `request_type` argument.

        :param entry: ``HarEntry`` object to analyze
        :param http_version: ``str`` of HTTP version type to match
        :param regex: ``bool`` indicating whether to use a regex or string match
        """
        response_version = entry.response.httpVersion
        if regex:
            return re.search(http_version, response_version,
                             flags=re.IGNORECASE) is not None
        else:
            return response_version == http_version

    @convert_to_entry
    def match_status_code(self, entry, status_code, regex=True):
        """
        Helper function that returns entries with a status code matching
        then given `status_code` argument.

        NOTE: This is doing a STRING comparison NOT NUMERICAL

        :param entry: entry object to analyze
        :param status_code: ``str`` of status code to search for
        :param regex: ``bool`` indicating whether to use a regex or string match
        """
        if regex:
            return re.search(status_code,
                             str(entry.response.status)) is not None
        else:
            return str(entry.response.status) == status_code

    def create_asset_timeline(self, asset_list):
        """
        Returns a ``dict`` of the timeline for the requested assets. The key is
        a datetime object (down to the millisecond) of ANY time where at least
        one of the requested assets was loaded. The value is a ``list`` of ALL
        assets that were loading at that time.

        :param asset_list: ``list`` of the assets to create a timeline for.
        """
        results = dict()
        for asset in asset_list:
            time_key = asset.startTime
            load_time = int(asset.time)
            # Add the start time and asset to the results dict
            if time_key in results:
                results[time_key].append(asset)
            else:
                results[time_key] = [asset]
            # For each millisecond the asset was loading, insert the asset
            # into the appropriate key of the results dict. Starting the range()
            # index at 1 because we already inserted the first millisecond.
            for _ in range(1, load_time):
                time_key = time_key + datetime.timedelta(milliseconds=1)
                if time_key in results:
                    results[time_key].append(asset)
                else:
                    results[time_key] = [asset]

        return results

    @property
    def pages(self):
        """
        This is a list of HarPage objects, each of which represents a page
        from the HAR file.
        """
        # Start with a page object for unknown entries if the HAR data has
        # any entries with no page ID
        pages = []
        if any('pageref' not in entry for entry in self.har_data['entries']):
            pages.append(HarPage('unknown', har_parser=self))
        for har_page in self.har_data['pages']:
            page = HarPage(har_page['id'], har_parser=self)
            pages.append(page)

        return pages

    @property
    def browser(self):
        return self.har_data['browser']

    @property
    def version(self):
        return self.har_data['version']

    @property
    def creator(self):
        return self.har_data['creator']

    @cached_property
    def hostname(self):
        valid_pages = [p for p in self.pages if p.page_id != 'unknown']
        return valid_pages[0].hostname


class HarPage(object):
    """
    An object representing one page of a HAR resource
    """

    def __init__(self, page_id, har_parser=None, har_data=None):
        """
        :param page_id: ``str`` of the page ID
        :param har_parser: a HarParser object
        :param har_data: ``dict`` of a file HAR file
        """
        self.page_id = page_id
        self._index = 0
        if har_parser is None and har_data is None:
            raise ValueError('Either parser or har_data is required')
        if har_parser:
            self.parser = har_parser
        else:
            self.parser = HarParser(har_data=har_data)

        # This maps the content type attributes to their respective regex
        # representations
        self.asset_types = {'image': 'image.*',
                            'css': '.*css',
                            'text': 'text.*',
                            'js': '.*javascript',
                            'audio': 'audio.*',
                            'video': 'video.*|.*flash',
                            'html': 'html',
                            }

        # Init properties that mimic the actual 'pages' object from the HAR file
        raw_data = self.parser.har_data
        valid = False
        if self.page_id == 'unknown':
            valid = True
        for page in raw_data['pages']:
            if page['id'] == self.page_id:
                valid = True
                self.title = page.get('title', '')
                self.startedDateTime = page['startedDateTime']
                self.pageTimings = page['pageTimings']

        if not valid:
            page_ids = [page['id'] for page in raw_data['pages']]
            raise PageNotFoundError(
                    'No page found with id {0}\n\nPage ID\'s are {1}'.format(
                            self.page_id, page_ids)
            )

    def __repr__(self):
        return 'ID: {0}, URL: {1}'.format(self.page_id, self.url)

    def __iter__(self):
        return iter(self.entries)

    def __next__(self):
        try:
            result = self.entries[self._index]
        except IndexError:
            raise StopIteration
        self._index += 1
        return result

    def _get_asset_files(self, asset_type):
        """
        Returns a list of all files of a certain type.
        """
        return self.filter_entries(content_type=self.asset_types[asset_type])

    def _get_asset_size_trans(self, asset_type):
        """
        Helper function to dynamically create *_size properties.
        """
        if asset_type == 'page':
            assets = self.entries
        else:
            assets = getattr(self, '{0}_files'.format(asset_type), None)
        return self.get_total_size_trans(assets)

    def _get_asset_size(self, asset_type):
        """
        Helper function to dynamically create *_size properties.
        """
        if asset_type == 'page':
            assets = self.entries
        else:
            assets = getattr(self, '{0}_files'.format(asset_type), None)
        return self.get_total_size(assets)

    def _get_asset_load(self, asset_type):
        """
        Helper function to dynamically create *_load_time properties. Return
        value is in ms.
        """
        if asset_type == 'initial':
            return self.actual_page.time
        elif asset_type == 'content':
            return self.pageTimings['onContentLoad']
        elif asset_type == 'page':
            if self.page_id == 'unknown':
                return None
            return self.pageTimings['onLoad']
            # TODO - should we return a slightly fake total load time to
            # accomodate HAR data that cannot understand things like JS
            # rendering or just throw a warning?
            #return self.get_load_time(request_type='.*',content_type='.*', status_code='.*', asynchronous=False)
        else:
            return self.get_load_time(
                content_type=self.asset_types[asset_type]
            )

    def filter_entries(
        self, request_type=None, content_type=None,  status_code=None,
        http_version=None, load_time__gt=None, regex=True
    ):
        """
        Returns a ``list`` of entry objects based on the filter criteria.

        :param request_type: ``str`` of request type (i.e. - GET or POST)
        :param content_type: ``str`` of regex to use for finding content type
        :param status_code: ``str`` of the desired status code
        :param http_version: ``str`` of HTTP version of request
        :param load_time__gt: ``int`` of a load time in milliseconds. If
            provided, an entry whose load time is less than this value will
            be excluded from the results.
        :param regex: ``bool`` indicating whether to use regex or exact match.
        """
        results = []

        for entry in self.entries:
            """
            So yea... this is a bit ugly. We are looking for:

                * The request type using self._match_request_type()
                * The content type using self._match_headers()
                * The HTTP response status code using self._match_status_code()
                * The HTTP version using self._match_headers()

            Oh lords of python.... please forgive my soul
            """
            valid_entry = True
            p = self.parser
            if request_type is not None and not p.match_request_type(
                    entry, request_type, regex=regex):
                valid_entry = False
            if content_type is not None:
                if not self.parser.match_content_type(entry, content_type,
                                                      regex=regex):
                    valid_entry = False
            if status_code is not None and not p.match_status_code(
                    entry, status_code, regex=regex):
                valid_entry = False
            if http_version is not None and not p.match_http_version(
                    entry, http_version, regex=regex):
                valid_entry = False
            if load_time__gt is not None and entry.time < load_time__gt:
                valid_entry = False

            if valid_entry:
                results.append(entry)

        return results

    def get_load_time(self, request_type=None, content_type=None,
                      status_code=None, asynchronous=True, **kwargs):
        """
        This method can return the TOTAL load time for the assets or the ACTUAL
        load time, the difference being that the actual load time takes
        asynchronous transactions into account. So, if you want the total load
        time, set asynchronous=False.

        EXAMPLE:

        I want to know the load time for images on a page that has two images,
        each of which took 2 seconds to download, but the browser downloaded
        them at the same time.

        self.get_load_time(content_types=['image']) (returns 2)
        self.get_load_time(content_types=['image'], asynchronous=False) (returns 4)
        """
        entries = self.filter_entries(
            request_type=request_type, content_type=content_type,
            status_code=status_code
        )

        if "async" in kwargs:
            asynchronous = kwargs['async']

        if not asynchronous:
            time = 0
            for entry in entries:
                time += entry.time
            return time
        else:
            return len(self.parser.create_asset_timeline(entries))

    def get_total_size(self, entries):
        """
        Returns the total size of a collection of entries.

        :param entries: ``list`` of entries to calculate the total size of.
        """
        size = 0
        for entry in entries:
            if entry.response.bodySize > 0:
                size += entry.response.bodySize
        return size

    def get_total_size_trans(self, entries):
        """
        Returns the total size of a collection of entries - transferred.

        NOTE: use with har file generated with chrome-har-capturer

        :param entries: ``list`` of entries to calculate the total size of.
        """
        size = 0
        for entry in entries:
            if entry.response.raw_entry['_transferSize'] > 0:
                size += entry.response.raw_entry['_transferSize']
        return size

    # BEGIN PROPERTIES #

    @cached_property
    def hostname(self):
        """
        Hostname of the initial request
        """
        for header in self.entries[0].request.headers:
            if header['name'] == 'Host':
                return header['value']

    @cached_property
    def url(self):
        """
        The absolute URL of the initial request.
        """
        if 'request' in self.entries[0].raw_entry and 'url' in self.entries[0].request.raw_entry:
            return self.entries[0].request.url
        return None

    @cached_property
    def entries(self):
        page_entries = []
        for entry in self.parser.har_data['entries']:
            if 'pageref' not in entry:
                if self.page_id == 'unknown':
                    page_entries.append(HarEntry(entry))
            elif entry['pageref'] == self.page_id:
                page_entries.append(HarEntry(entry))
        # Make sure the entries are sorted chronologically
        if all(x.startTime for x in page_entries):
            return sorted(page_entries, key=lambda entry: entry.startTime)
        else:
            return page_entries

    @cached_property
    def time_to_first_byte(self):
        """
        Time to first byte of the page request in ms
        """
        # The unknown page is just a placeholder for entries with no page ID.
        # As such, it would not have a TTFB
        if self.page_id == 'unknown':
            return None
        ttfb = 0
        for entry in self.entries:
            if entry.response.status == 200:
                for k, v in iteritems(entry.timings):
                    if k != 'receive':
                        if v > 0:
                            ttfb += v
                break
            else:
                ttfb += entry.time

        return ttfb

    @cached_property
    def get_requests(self):
        """
        Returns a list of GET requests, each of which is an 'entry' data object
        """
        return self.filter_entries(request_type='get')

    @cached_property
    def post_requests(self):
        """
        Returns a list of POST requests, each of which is an 'entry' data object
        """
        return self.filter_entries(request_type='post')

    # FILE TYPE PROPERTIES #

    @cached_property
    def actual_page(self):
        """
        Returns the first entry object that does not have a redirect status,
        indicating that it is the actual page we care about (after redirects).
        """
        for entry in self.entries:
            if not (300 <= entry.response.status <= 399):
                return entry

    @cached_property
    def duplicate_url_request(self):
        """
        Returns a dict of urls and its number of repetitions that are sent more than once
        """
        urls = [entry.request.url for entry in self.entries]
        counted_urls = Counter(urls)
        return {k: v for k, v in counted_urls.items() if v > 1}

    # Convenience properties. Easy accessible through the API, but even easier
    # to use as properties
    @cached_property
    def image_files(self):
        return self._get_asset_files('image')

    @cached_property
    def css_files(self):
        return self._get_asset_files('css')

    @cached_property
    def text_files(self):
        return self._get_asset_files('text')

    @cached_property
    def js_files(self):
        return self._get_asset_files('js')

    @cached_property
    def audio_files(self):
        return self._get_asset_files('audio')

    @cached_property
    def video_files(self):
        return self._get_asset_files('video')

    @cached_property
    def html_files(self):
        return self._get_asset_files('html')

    @cached_property
    def page_size(self):
        return self._get_asset_size('page')

    @cached_property
    def image_size(self):
        return self._get_asset_size('image')

    @cached_property
    def css_size(self):
        return self._get_asset_size('css')

    @cached_property
    def text_size(self):
        return self._get_asset_size('text')

    @cached_property
    def js_size(self):
        return self._get_asset_size('js')

    @cached_property
    def audio_size(self):
        return self._get_asset_size('audio')

    @cached_property
    def video_size(self):
        return self._get_asset_size('video')

    @cached_property
    def page_size_trans(self):
        return self._get_asset_size_trans('page')

    @cached_property
    def image_size_trans(self):
        return self._get_asset_size_trans('image')

    @cached_property
    def css_size_trans(self):
        return self._get_asset_size_trans('css')

    @cached_property
    def text_size_trans(self):
        return self._get_asset_size_trans('text')

    @cached_property
    def js_size_trans(self):
        return self._get_asset_size_trans('js')

    @cached_property
    def audio_size_trans(self):
        return self._get_asset_size_trans('audio')

    @cached_property
    def video_size_trans(self):
        return self._get_asset_size_trans('video')

    @cached_property
    def initial_load_time(self):
        return self._get_asset_load('initial')

    @cached_property
    def content_load_time(self):
        return self._get_asset_load('content')

    @cached_property
    def page_load_time(self):
        return self._get_asset_load('page')

    @cached_property
    def image_load_time(self):
        return self._get_asset_load('image')

    @cached_property
    def css_load_time(self):
        return self._get_asset_load('css')

    @cached_property
    def js_load_time(self):
        return self._get_asset_load('js')

    @cached_property
    def audio_load_time(self):
        return self._get_asset_load('audio')

    @cached_property
    def video_load_time(self):
        return self._get_asset_load('video')

    @cached_property
    def html_load_time(self):
        return self._get_asset_load('html')


class HarEntry(MimicDict):
    """
        An object that represent one entry in a HAR Page
    """
    def __init__(self, entry):
        self.raw_entry = entry

    def __str__(self):
        return "HarEntry for %s" % self.raw_entry["request"]["url"]

    def __repr__(self):
        return "HarEntry for %s" % self.raw_entry["request"]["url"]

    @cached_property
    def request(self):
        return Request(entry=self.raw_entry["request"])

    @cached_property
    def response(self):
        if isinstance(self.raw_entry, dict):
            return Response(entry=self.raw_entry["response"])
        return self.raw_entry.response

    @cached_property
    def startTime(self):
        try:
            return parser.parse(self.raw_entry.get("startedDateTime", ""))
        except parser._parser.ParserError:
            return None

    @cached_property
    def cache(self):
        return self.raw_entry["cache"]

    @cached_property
    def cookies(self):
        return self.raw_entry.get("cookies", [])

    @cached_property
    def pageref(self):
        return self.raw_entry["pageref"]

    @cached_property
    def port(self):
        return int(self.raw_entry["connection"])

    @cached_property
    def secure(self):
        return self.raw_entry.get('_securityState', '') == 'secure'

    @cached_property
    def serverAddress(self):
        return self.raw_entry["serverIPAddress"]

    @cached_property
    def status(self):
        return self.raw_entry["response"]["status"]

    @cached_property
    def time(self):
        return self.raw_entry["time"]

    @cached_property
    def timings(self):
        return self.raw_entry["timings"]

    @cached_property
    def url(self):
        return self.raw_entry["request"]["url"]
