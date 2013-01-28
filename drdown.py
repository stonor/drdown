#!/usr/bin/python

import sys
import os
import re
import logging
import json

if sys.version_info < (3, 0):
    from urllib2 import Request
    from urllib2 import urlopen
    from urlparse import urlparse

else:
    from urllib.request import Request
    from urllib.request import urlopen
    from urllib.parse import urlparse
    raw_input = input
    

useragent = 'Mozilla/5.0'
headers = {'User-Agent': useragent}

intro = """
Usage: drdown.py url

This script finds the stream URL from a dr.dk page so you can
download the tv program.
"""


def fetch(url):
    """Download body from url"""

    req = Request(url, headers=headers)
    response = urlopen(req)
    body = response.read()
    response.close()

    # convert to string - it is easier to convert here
    if isinstance(body, bytes):
        body = body.decode('utf8')
    
    return body


class StreamExtractor:

    def __init__(self, url):
        self.url = url.lower()
        self.urlp = urlparse(self.url)

    def get_stream_data(self):
        """supply a URL to a video page on dr.dk and get back a streaming
        url"""

        if self.urlp.netloc not in ('dr.dk', 'www.dr.dk'):
            raise Exception("Must be an URL from dr.dk")

        self.html = fetch(self.url)
        logging.info("Player fetched: " + self.url)

        # Standalone show?
        if self.urlp.path.startswith('/tv/se/'):
            return self.get_stream_data_from_standalone()

        # Bonanza?
        elif self.urlp.path.startswith('/bonanza/'):
            return self.get_stream_data_from_bonanza()

        # Live tv?
        elif self.urlp.path.startswith('/tv/live'):
            return self.get_stream_data_from_live()

        else:
            return self.get_stream_data_from_series()

    def get_stream_data_from_rurl(self, rurl):
        """Helper method to parse resource JSON document"""

        body = fetch(rurl)
        resource_data = json.loads(body)

        qualities = resource_data.get('links')
        # sort by quality
        qualities = sorted(qualities, key=lambda x: x['bitrateKbps'],
                           reverse=True)
        stream_data = qualities[0]
        stream_url = stream_data.get('uri')

        logging.info("Stream data fetched: " + stream_url)
        playpath, filename = self.get_metadata_from_stream_url(stream_url)
        stream_data = {'stream_url': stream_url,
                         'playpath': playpath,
                         'filename': filename,
                         'is_live': False}

        return stream_data

    def get_metadata_from_stream_url(self, stream_url):
        """Helper method to extacts playpath and filename suggestion from a
        rtmp url"""

        parsed = urlparse(stream_url)
        playpath_s = parsed.path.split('/')[2:]
        playpath = '/'.join(playpath_s)

        # rerun to split the parameter
        path = urlparse(parsed.path).path
        filename = path.split('/')[-1]
        return playpath, filename

    def get_stream_data_from_standalone(self):
        """Extracts stream data from a normal single program page.
        The data is hidden in a resource URL, that we need to download
        and parse.
        """

        mu_regex = re.compile('resource: "([^"]+)"')
        m = mu_regex.search(self.html)
        if m and m.groups():
            resource_meta_url = m.groups()[0]
            return self.get_stream_data_from_rurl(resource_meta_url)

    def get_stream_data_from_bonanza(self):
        """Finds stream URL from bonanza section. Just pick up the first RTMP
        url.
        """

        stream_regex = re.compile('rtmp://.*?\.mp4')
        m = stream_regex.search(self.html)
        if m and m.group():
            stream_url = m.group()
        else:
            raise Exception("Could not find Bonanza stream URL")

        playpath, filename = self.get_metadata_from_stream_url(stream_url)
        stream_data = {'stream_url': stream_url,
                       'playpath': playpath,
                       'filename': filename,
                       'is_live': False}
        return stream_data

    def get_stream_data_from_live(self):
        stream_url = 'rtmp://livetv.gss.dr.dk/live'
        quality = '3'

        playpaths = {'dr1': 'livedr01astream',
                     'dr2': 'livedr02astream',
                     'dr-ramasjang': 'livedr05astream',
                     'dr-k': 'livedr04astream',
                     'dr-update-2': 'livedr03astream',
                     'dr3': 'livedr06astream'}

        urlend = self.urlp.path.split('/')[-1]
        playpath = playpaths.get(urlend)
        filename = 'live.mp4'
        if playpath:
            playpath += quality
            filename = urlend + '.mp4'

        stream_data = {'stream_url': stream_url,
                       'playpath': playpath,
                       'filename': filename,
                       'is_live': True}

        return stream_data

    def get_stream_data_from_series(self):
        """dr.dk has a special player for multi episode series. This is the
        fall back parser, as there seems to be no pattern in the URL."""

        slug_regex = re.compile('seriesSlug=([^"]+)"')

        m = slug_regex.search(self.html)
        if m and m.groups():
            slug_id = m.groups()[0]
        else:
            raise Exception("Could not find program slug")

        logging.info("found slug: " + slug_id)
        program_meta_url = 'http://www.dr.dk/nu/api/programseries/%s/videos'\
            % slug_id
        body = fetch(program_meta_url)
        program_data = json.loads(body)
        if not program_data:
            raise Exception("Could not find data about the program series")

        fragment = self.urlp.fragment
        if fragment.startswith('/'):
            fragment = fragment[1:]
        fragment = fragment.split('/')
        video_id = fragment[0]

        logging.info("Video ID: " + video_id)
        video_data = None
        if video_id:
            for item in program_data:
                if item['id'] == int(video_id):
                    video_data = item

        if not video_data:
            video_data = program_data[0]

        resource_meta_url = video_data.get('videoResourceUrl')
        return self.get_stream_data_from_rurl(resource_meta_url)

    def generate_cmd(self):
        """Build command line to download stream with the rtmpdump tool"""

        sdata = self.get_stream_data()
        if not sdata:
            return "Not found"

        filename = sdata['filename']
        custom_filename = raw_input("Type another filename or press <enter> to keep default [%s]: " % filename)

        if custom_filename:
            filename = custom_filename

        cmd_live = 'rtmpdump --live --rtmp="%s" --playpath="%s" -o %s'
        cmd_rec = 'rtmpdump -e --rtmp="%s" --playpath="%s" -o %s'
        if sdata['is_live'] is True:
            cmd = cmd_live % (sdata['stream_url'], sdata['playpath'], filename)
        else:
            cmd = cmd_rec % (sdata['stream_url'], sdata['playpath'], filename)

        return cmd


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
        extractor = StreamExtractor(url)
        cmd = extractor.generate_cmd()
        os.system(cmd)

    else:
        print(intro)


if __name__ == "__main__":
    main()
