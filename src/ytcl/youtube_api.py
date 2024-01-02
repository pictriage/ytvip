from . import common
import asyncio
from aiohttp import ClientSession
import re
from urllib.request import urlopen
from urllib.parse import urlencode, urlparse
import urllib
from icecream import ic
import json
import urllib.error
from .common import YOUTUBE_API_KEY
from pathlib import Path

print_function = print

scopes = ["https://www.googleapis.com/auth/youtube.readonly"]


def yt_request(resource, params):
    params = dict(params, key=YOUTUBE_API_KEY)
    querystring = urlencode(params)
    url = f"https://www.googleapis.com/youtube/v3/{resource}?{querystring}"
    try:
        with urlopen(url) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print_function(e.code)
        print_function(json.loads(e.read()))
        raise


class ChannelNotFoundError(Exception):
    pass


def get_channel(**kwargs):
    resp = yt_request(
        'channels', dict(part="snippet,contentDetails,statistics", **kwargs)
    )
    try:
        [channel] = resp['items']
        return channel
    except KeyError as exc:
        raise ChannelNotFoundError from None


class Text:

    from unicodedata import normalize

    # https://stackoverflow.com/a/29247821/8327971

    @staticmethod
    def normalize_casefold(text):
        return Text.normalize("NFKD", text.casefold())

    @staticmethod
    def casefold_equal(text1, text2):
        return Text.normalize_casefold(text1) == Text.normalize_casefold(text2)


YOUTUBE_VIDEOS_PER_PAGE = 50


async def list_videos_by_page(channel_id):

    channel = get_channel(id=channel_id)
    playlistId = channel['contentDetails']['relatedPlaylists']['uploads']
    pageToken = ''
    num_processed = 0

    while True:
        resp = yt_request(
            'playlistItems',
            dict(
                part="snippet,status,contentDetails",
                playlistId=playlistId,
                pageToken=pageToken,
                maxResults=YOUTUBE_VIDEOS_PER_PAGE,
            ),
        )

        total_num_results = resp['pageInfo']['totalResults']
        pageToken = resp.get('nextPageToken')

        ids = [item['contentDetails']['videoId'] for item in resp['items']]
        resp = yt_request(
            'videos',
            dict(
                part="snippet,status,contentDetails,statistics,player",
                id=','.join(ids),
                # pageToken=pageToken2,
                maxResults=50,
            ),
        )

        items = resp['items']

        ytids = [d1['id'] for d1 in items]

        await common.download_video_thumbnails(channel_id, ytids)

        yield items

        # can't request fileDetails because that is only available to the owner
        num_processed += len(resp['items'])

        if num_processed == total_num_results:
            break


def get_channel_from_video_url(url):
    parsed = urlparse(url)
    # breakpoint()
    ytid = urllib.parse.parse_qs(parsed.query)['v'][0]
    resp = yt_request('videos', dict(part='snippet', id=ytid))
    channel_id = resp['items'][0]['snippet']['channelId']
    channel_data = get_channel(id=channel_id)
    return dict(
        id=channel_data['id'],
        name=channel_data['snippet']['title'],
        thumbnail_url=channel_data['snippet']['thumbnails']['high']['url'],
    )
