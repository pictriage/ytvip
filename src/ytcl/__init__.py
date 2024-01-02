import itertools
import json
import logging
import math
import os
import random
import subprocess
import sys
import time
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
import argparse

import ibis
import peewee
from ibis.nodes import register, Node, Expression
from icecream import ic
from starlette.applications import Starlette
from starlette.endpoints import HTTPEndpoint
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    Response,
    StreamingResponse,
    RedirectResponse,
)
from starlette.routing import Mount
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from . import tasks
from . import youtube_api
from .common import (
    VIDEOS_ROOT,
    FILES_ROOT,
    call,
    path2url,
    download_video_thumbnail,
    download_video_thumbnails,
    SUBCOMMANDS,
    YTIDS_TO_IGNORE,
    BRAND_NAME,
    CMD_NAME,
    TERMS_TO_HIGHLIGHT,
    convert_iso8601,
)
from .models import (
    db,
    Channel,
    Video,
    QueuedTask,
    get_downloaded_paths,
    get_all_downloaded_paths,
    get_all_preview_paths,
    IgnoreTerm,
    DOWNLOAD_STATUS,
)
from . import youtube_api

print_function = print

loader = ibis.loaders.FileReloader(Path(__file__).parent.joinpath("templates"))


@register("static")
class StaticNode(Node):
    def process_token(self, token):
        _, path = token.text.split()
        self.path_expr = Expression(path, token)

    def wrender(self, context):
        return app.router.url_path_for("static", path=self.path_expr.eval(context))


@register("url")
class UrlNode(Node):
    """
    Limitation is that this doesn't take args yet.
    That would take more complex parsing.
    url_path_for doesn't accept positional args, only kwargs
    """

    def process_token(self, token):
        _, url_name = token.text.split()
        self.url_name_expr = Expression(url_name, token)

    def wrender(self, context):
        return app.router.url_path_for(self.url_name_expr.eval(context))


def render_to_response(template_name, ctx) -> HTMLResponse:
    template = loader(template_name)
    return HTMLResponse(template.render(ctx, strict_mode=True))


class Index(HTTPEndpoint):
    def get(self, request: Request):
        channels = Channel.select().order_by(Channel.local_view_count.desc())
        channels = list(channels)
        channels.sort(
            key=lambda c: (c.local_view_count, c.num_local_videos()), reverse=True
        )
        for c in channels:
            c: Channel
            tmp_preview_videos = list(itertools.islice(c.urls_of_preview_videos(), 2))
            if tmp_preview_videos:
                c.tmp_display_orientation = tmp_preview_videos[0].display_orientation()
            c.tmp_preview_videos = tmp_preview_videos
        ctx = dict(
            channels=channels,
            FORCE_VERTICAL=common.FORCE_VERTICAL,
            BRAND_NAME=BRAND_NAME,
        )
        return render_to_response("Index.html", ctx)


class AddChannel(HTTPEndpoint):
    def get(self, request):
        return render_to_response("AddChannel.html", dict(BRAND_NAME=BRAND_NAME))

    async def post(self, request: Request):
        form_data = await request.form()
        video_url = form_data["video_url"]
        auto_download_previews = bool(form_data.get("auto_download_previews"))

        channel_data = youtube_api.get_channel_from_video_url(video_url)

        try:
            channel = Channel.create(
                **channel_data,
                auto_download_previews=auto_download_previews,
            )
        except peewee.IntegrityError:
            return RedirectResponse(app.url_path_for("Index"), status_code=303)
        channel.download_thumbnail()
        channel.video_dir().mkdir(exist_ok=True)
        channel.preview_video_dir().mkdir(exist_ok=True)
        channel.preview_video_dir_shorter().mkdir(exist_ok=True)
        channel.thumbnail_dir().mkdir(exist_ok=True)
        return RedirectResponse(channel.populate_videos_url(), status_code=303)


PAGE_SIZE = 500


class SORT_BY:
    DATE = 'date'
    BEST = 'best'


class BrowseChannel(HTTPEndpoint):
    def get(self, request: Request):
        channel_id = request.path_params["channel_id"]
        page_number = int(request.query_params.get("page", "1"))
        channel = Channel.get_by_id(channel_id)
        downloaded_ytids = set(p.stem for p in channel.local_video_paths())
        preview_ytids = set(p.stem for p in channel.preview_video_paths())

        model_videos = [
            v
            # just because you viewed a video doesn't mean you like it
            for v in channel.videos(
                order_by=[
                    Video.score.desc(),
                    Video.local_view_count.desc(),
                    Video.yt_like_count.desc(),
                ]
            )
        ]

        downloaded = []
        favorites = []
        least_favorites = []
        rest = []
        recent = []
        low_res = []

        for video in model_videos:
            if video.ytid in downloaded_ytids:
                downloaded.append(video)
            # elif video.is_1080p_or_lower():
            #     low_res.append(video)
            elif video.is_recent():
                recent.append(video)
            elif video.score > 0:
                favorites.append(video)
            elif video.score < 0:
                least_favorites.append(video)
            else:
                rest.append(video)

        # combine them into 1 section to make pagination
        # easier
        rest += least_favorites

        sort_by = (
            request.query_params.get("sort_by")
            or request.cookies.get("sort_by")
            or SORT_BY.DATE
        )

        sort_by_options = {k: False for k in [SORT_BY.BEST, SORT_BY.DATE]}
        sort_by_options[sort_by] = True

        if sort_by == SORT_BY.BEST:
            # need this because we can't use view counts, etc.,
            # for videos we haven't downloaded yet.

            rest.sort(key=lambda v: v.yt_view_count, reverse=True)
            chunk_size = len(rest) // 5
            for chunk_num in range(5):
                i = chunk_num * chunk_size
                j = i + chunk_size
                rest[i:j] = sorted(rest[i:j], key=lambda v: v.views_per_like())

            # rest.sort()

            by_views_per_like = {}
            for i, v in enumerate(sorted(rest, key=lambda v: v.views_per_like())):
                by_views_per_like[v.ytid] = i
            by_like_count = {}
            for i, v in enumerate(
                sorted(rest, key=lambda v: v.yt_like_count or 0, reverse=True)
            ):
                by_like_count[v.ytid] = i

            # absolute like count favors older videos that had more time to get views
            rest.sort(
                key=lambda v: by_views_per_like[v.ytid] + 3 * by_like_count[v.ytid]
            )
        else:
            rest.sort(key=lambda v: v.published_at, reverse=True)
            downloaded.sort(key=lambda v: v.published_at, reverse=True)
        recent.sort(key=lambda v: v.published_at, reverse=True)

        page_start_at = PAGE_SIZE * (page_number - 1)
        page_stop_at = page_start_at + PAGE_SIZE
        max_page_number = math.ceil(len(rest) / PAGE_SIZE)
        page_numbers = list(range(1, max_page_number + 1))

        rest = rest[page_start_at:page_stop_at]

        if page_number > 1:
            downloaded = []
            recent = []
            favorites = []

        sections = dict(
            Downloaded=downloaded,
            Recent=recent,
            Favorites=favorites,
            Rest=rest,
            # before we just hid these videos but it's a big gotcha...when videos
            # exist but are not shown anywhere. better to put them at a bottom
            # section.
            Low_Res=low_res,
        )

        ignore_terms = IgnoreTerm.all_terms()
        section_htmls = {}
        for name, videos in sections.items():
            if videos:
                htmls = []
                for video in videos:
                    html = mk_video_html(
                        video,
                        downloaded_ytids=downloaded_ytids,
                        show_static_thumbnails=get_show_static_thumbnails(request),
                        preview_version_ytids=preview_ytids,
                        ignore_terms=ignore_terms,
                    )
                    if html:
                        htmls.append(html)
                if htmls:
                    section_htmls[name] = htmls

        if common.FORCE_VERTICAL:
            regular_orientation_count = len(
                get_downloaded_paths(channel=channel, orientation='vert')
            )

            htov_count = len(get_downloaded_paths(channel=channel, orientation='horz'))
        else:
            regular_orientation_count = len(get_downloaded_paths())
            htov_count = 0

        resp = render_to_response(
            "BrowseChannel.html",
            dict(
                section_htmls=section_htmls,
                regular_orientation_count=regular_orientation_count,
                htov_count=htov_count,
                channel=channel,
                sort_by_options=sort_by_options,
                show_static_thumbnails=get_show_static_thumbnails(request),
                page_numbers=page_numbers,
                page_number=page_number,
                FORCE_VERTICAL=common.FORCE_VERTICAL,
                BRAND_NAME=BRAND_NAME,
            ),
        )
        resp.set_cookie("sort_by", sort_by)
        return resp


def is_ignorable(title, ignore_terms):
    return any(s.lower() in title.lower() for s in ignore_terms)


async def wrapper_for_fetch_generator(channels, downloaded_ytids, first_page_only=True):
    try:
        async for chunk in video_fetch_generator(
            channels, downloaded_ytids, first_page_only
        ):
            yield chunk
    except Exception:
        yield "<p>Error. Check the server logs.</p>"
        raise


async def video_fetch_generator(channels, downloaded_ytids, first_page_only=True):
    # item['contentDetails']['videoId']

    yield f"""
    <html>
    <head><title>{BRAND_NAME}: Update from YouTube</title>
    
    <link rel="stylesheet" href="{static('common.css')}">
    </head>
    <body>
    <h1><a href="/">{BRAND_NAME}</a> > Update from YouTube</h1>
    <p>Please wait, this may take a few minutes.</p>
    <script src="{static('jquery.min.js')}"></script>
    <script src="{static('common.js')}"></script>
    <script src="{static('htmx.min.js')}"></script>
    """
    FLEX_DIV_BEGIN = """<div class="gallery">"""
    FLEX_DIV_END = """</div>"""

    ignore_terms = IgnoreTerm.all_terms()

    channel_page_generators = {
        channel: youtube_api.list_videos_by_page(channel.id) for channel in channels
    }

    model_videos = {v.ytid: v for v in Video.select()}

    yield FLEX_DIV_BEGIN

    # run the searches in parallel so that we get some variety
    # in downloaded content.
    videos_processed = 0
    while channel_page_generators:
        for channel, gen in list(channel_page_generators.items()):
            try:
                page = await gen.__anext__()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    del channel_page_generators[channel]
                    continue
            # channel could have been deleted.
            except StopAsyncIteration:
                del channel_page_generators[channel]
                continue
            except youtube_api.ChannelNotFoundError:
                del channel_page_generators[channel]
                yield f"<p>Channel not found on YouTube: {channel.name}.</p>"
                continue

            new_d1s = []
            for d1 in page:
                ytid = d1["id"]
                if ytid in model_videos:
                    video = model_videos[ytid]

                    for k, v in mk_video_model_fields(d1).items():
                        setattr(video, k, v)
                    video.save()
                    # we update the stats, but don't show the videos.
                    # that makes it clearer to see what videos are new,
                    # without having to mark them somehow.
                    # videos getting stats updated is a side effect.
                    # you can ensure stats are updated by loading tha channel
                    # and waiting for it to complete.
                    # if video.is_recent():
                    #     videos_to_show.append(video)
                else:
                    if is_ignorable(d1["snippet"]["title"], ignore_terms):
                        continue
                    new_d1s.append(d1)
                    ytid = d1["id"]

                    video = Video.create(
                        **mk_video_model_fields(d1),
                        channel=channel,
                        ytid=ytid,
                    )
                    # it might be overkill to download the 1-second previews
                    # for all videos. you might have a huge number of channels/videos,
                    # and those 1-second videos are not useful in all cases.
                    # maybe we should have a button specifically for that.
                    if channel.auto_download_previews:
                        video.schedule_download_preview()

                    # before i put mk_video_html calls in a separate loop
                    # after the were all loaded from yt-dlp,
                    # but it's better to do it immediately so there's no waiting
                    # until results start showing.
                    html = mk_video_html(
                        video,
                        downloaded_ytids=downloaded_ytids,
                        ignore_terms=ignore_terms,
                        show_static_thumbnails=True,
                        show_channel=True,
                        preview_version_ytids=[],
                    )
                    if html:
                        yield html
            videos_processed += youtube_api.YOUTUBE_VIDEOS_PER_PAGE
            yield f"<p>Checked {videos_processed} newest videos...</p>"
        if first_page_only:
            break

    yield FLEX_DIV_END
    yield "<p>Done updating.</p>"

    yield "</body></html>"


class Search(HTTPEndpoint):
    def get(self, request: Request):
        search_term = request.query_params["search_term"].strip()
        num_results = 0
        downloaded_ytids = set(p.stem for p in get_all_downloaded_paths())
        downloaded_video_htmls = []
        video_htmls = []

        preview_ytids = set()
        for root in [common.PREVIEW_ROOT, common.PREVIEW_SHORT_ROOT]:
            preview_ytids.update([p.stem for p in root.glob("**/*.*")])

        order_by_date_str = request.query_params.get(
            SEARCH_ORDER_BY_DATE_COOKIE
        ) or request.cookies.get(SEARCH_ORDER_BY_DATE_COOKIE, "0")
        order_by_date_bool = order_by_date_str == "1"

        channels = Channel.select()

        for channel in channels:
            channel.tmp_is_included = True

        filter_widget_expanded = False
        if search_term:
            ignore_terms = IgnoreTerm.all_terms()

            word1, *rest = search_term.split()
            where_clause = [Video.title.contains(word1)]
            for word in rest:
                # why use the bitwise op? why not pass a *list to .where?
                where_clause.append(Video.title.contains(word))

            channels_to_include = request.query_params.get("channels_to_include")
            channels_to_exclude = request.query_params.get("channels_to_exclude")

            date_min = request.query_params.get('date_min')
            date_max = request.query_params.get('date_max')

            if date_min:
                where_clause.append(Video.published_at >= parse_html_date_input(date_min))
            if date_max:
                where_clause.append(Video.published_at <= parse_html_date_input(date_max))

            if channels_to_include:
                where_clause.append(Video.channel_id << channels_to_include.split(","))
                for channel in channels:
                    channel.tmp_is_included = channel.id in channels_to_include
                filter_widget_expanded = True

            elif channels_to_exclude:
                where_clause.append(Video.channel_id.not_in(channels_to_exclude.split(",")))
                for channel in channels:
                    channel.tmp_is_included = channel.id not in channels_to_exclude
                filter_widget_expanded = True

            if order_by_date_bool:
                order_by = Video.published_at.desc()
            else:
                order_by = Video.yt_like_count.desc()

            videos = [v for v in Video.select().where(*where_clause).order_by(order_by)]

            for video in videos:
                html = mk_video_html(
                    video,
                    downloaded_ytids=downloaded_ytids,
                    ignore_terms=ignore_terms,
                    show_channel=True,
                    preview_version_ytids=preview_ytids,
                    show_static_thumbnails=get_show_static_thumbnails(request),
                )
                if html:
                    num_results += 1
                    if video.ytid in downloaded_ytids:
                        downloaded_video_htmls.append(html)
                    else:
                        video_htmls.append(html)
        else:
            date_min = ''
            date_max = ''

        ctx = dict(
            video_htmls=video_htmls,
            search_term=search_term,
            num_results=num_results,
            downloaded_video_htmls=downloaded_video_htmls,
            order_by_date_bool=order_by_date_bool,
            channels=channels,
            filter_widget_expanded=filter_widget_expanded,
            BRAND_NAME=BRAND_NAME,
            date_min=date_min,
            date_max=date_max,
        )

        resp = render_to_response("Search.html", ctx)
        resp.set_cookie(SEARCH_ORDER_BY_DATE_COOKIE, order_by_date_str)
        return resp

def parse_html_date_input(value) -> datetime:
    yyyy, mm, dd = value.split('-')
    return datetime(year=int(yyyy), month=int(mm), day=int(dd))

class UpdateFromYouTube(HTTPEndpoint):
    async def get(self, request: Request):
        """
        it should stop
        when the user clicks 'stop' in their browser, or closes the tab.
        https://github.com/encode/starlette/issues/854
        """
        channel_id = request.query_params.get("channel_id")
        if channel_id:
            channels = [Channel.get(id=channel_id)]
            first_page_only = False
        else:
            channels = list(Channel.select())
            # prioritize your favorite channels
            channels.sort(key=lambda c: c.local_view_count, reverse=True)
            first_page_only = True
        downloaded_ytids = set(p.stem for p in get_all_downloaded_paths())
        return StreamingResponse(
            wrapper_for_fetch_generator(
                channels,
                downloaded_ytids,
                first_page_only=first_page_only,
            ),
            media_type="text/html",
        )


class RecentlyPublished(HTTPEndpoint):
    def get(self, request: Request):
        downloaded_ytids = set(p.stem for p in get_all_downloaded_paths())
        preview_ytids = set(p.stem for p in get_all_preview_paths())

        ignore_terms = IgnoreTerm.all_terms()

        videos = (Video.select()
            .order_by(Video.published_at.desc())
                  )[:300]

        htmls = []
        for video in videos:
            html = mk_video_html(
                video,
                downloaded_ytids=downloaded_ytids,
                show_static_thumbnails=get_show_static_thumbnails(request),
                ignore_terms=ignore_terms,
                show_channel=True,
                preview_version_ytids=preview_ytids,
            )
            if html:
                htmls.append(html)

        return render_to_response(
            "RecentlyPublished.html",
            dict(
                video_htmls=htmls,
                BRAND_NAME=BRAND_NAME,
            ),
        )


class Downloads(HTTPEndpoint):
    def get(self, request: Request):
        downloaded_ytids = set(p.stem for p in get_all_downloaded_paths())
        preview_ytids = set(p.stem for p in get_all_preview_paths())

        ignore_terms = IgnoreTerm.all_terms()

        videos = (Video.select()
            .where(Video.download_status_epoch.is_null(False))
            .order_by(Video.download_status_epoch.desc())
                  )[:100]

        htmls = []
        show_static_thumbnails = get_show_static_thumbnails(request)


        for video in videos:
            html = mk_video_html(
                video,
                downloaded_ytids=downloaded_ytids,
                ignore_terms=ignore_terms,
                show_channel=True,
                show_static_thumbnails=show_static_thumbnails,
                preview_version_ytids=preview_ytids,
            )
            if html:
                htmls.append(html)

        return render_to_response(
            "Downloads.html",
            dict(
                video_htmls=htmls,
                BRAND_NAME=BRAND_NAME,
            ),
        )


def mk_video_html(
    video: Video,
    *,
    downloaded_ytids,
    ignore_terms,
    show_static_thumbnails,
    preview_version_ytids,
    show_channel=False,
):
    ytid = video.ytid
    is_downloaded = ytid in downloaded_ytids

    # put these guards inside the function becuase then we only have to
    # write this code once, rathen than everywhere this function is called from.
    if YTIDS_TO_IGNORE and (ytid in YTIDS_TO_IGNORE):
        return
    if is_ignorable(video.title, ignore_terms) and (not is_downloaded):
        return

    if not preview_version_ytids:
        preview_version_ytids = []
    # if video.is_1080p_or_lower():
    #     # let's not even waste our time creating an instance, downloading the thumbnail
    #     # etc. but this is a bit of a gotcha.
    #     return
    dt = video.published_at
    published_at = f"{dt.year}-{dt.month}-{dt.day}"
    mm, ss = divmod(video.duration, 60)
    bullets = dict(
        published_at=published_at,
        duration=f"{mm}:{ss:02d}",
    )
    if video.height:
        bullets["format"] = f"{video.width}x{video.height} @ {video.fps}"

    title: str = video.title

    if TERMS_TO_HIGHLIGHT:
        for term in TERMS_TO_HIGHLIGHT:
            title = title.replace(term, f'<span class="highlight-term">{term}</span>')

    bullets.update(
        yt_view_count="{:,.0f}".format(round(video.yt_view_count, -3)),
        views_per_like="?"
        if video.yt_like_count is None
        else int(video.views_per_like()),
        # put title last because it breaks lines
        title=title,
    )

    if show_channel:
        url = app.url_path_for("BrowseChannel", channel_id=video.channel.id)
        bullets["channel"] = f"""<a href="{url}">{video.channel.name}</a>"""

    # if is_downloaded:
    #     video_url = path2url(video.file_path())
    # else:
    #     video_url = ''

    if (not show_static_thumbnails) and ytid in preview_version_ytids:
        preview_url = path2url(video.preview_file_path())
    else:
        preview_url = ""

    if common.FORCE_VERTICAL:
        if video.height:
            is_portrait = video.height > video.width
        else:
            is_portrait = None
        if is_portrait:
            download_icon = '▮'
        else:
            download_icon = '▭'
    else:
        download_icon = '⭳'
    thumbnail_path = video.thumbnail_path()

    if thumbnail_path.exists():
        thumbnail_url = path2url(thumbnail_path)
    else:
        # this happened for me with a private video.
        thumbnail_url = app.router.url_path_for("static", path="missing-thumbnail.jpg")

    return loader("video.html").render(
        dict(
            bullets=bullets,
            video=video,
            is_downloaded=is_downloaded,
            download_icon=download_icon,
            thumbnail_url=thumbnail_url,
            # video_url=video_url,
            preview_url=preview_url,
        ),
        strict_mode=True,
    )


def static(path):
    return app.router.url_path_for("static", path=path)


def mk_video_model_fields(d1) -> dict:
    return dict(
        duration=convert_iso8601(d1["contentDetails"]["duration"]),
        title=d1["snippet"]["title"],
        published_at=datetime.fromisoformat(d1["snippet"]["publishedAt"][:-1]),
        yt_view_count=int(d1["statistics"]["viewCount"]),
        yt_like_count=int(d1["statistics"].get("likeCount", 0)),
    )


class Download(HTTPEndpoint):
    async def post(self, request: Request):
        data = await request.json()
        channel_id = data["channel_id"]
        ytid = data["ytid"]

        channel = Channel.get(id=channel_id)

        video = Video.get(ytid=ytid)
        video.set_download_status(DOWNLOAD_STATUS.QUEUED)
        video.save()

        QueuedTask.create(
            operation='download',
            # downloading should be higher pri because
            # it means you explicitly want that video.
            priority=5,
            kwargs_json=json.dumps(dict(
                ytid=ytid,
                channel_dir=str(channel.video_dir()),
                preview_channel_dir=str(channel.preview_video_dir()),
            ))
        )

        return Response("")


class ChangeScore(HTTPEndpoint):
    async def post(self, request: Request):
        form = await request.form()
        Video.update(score=int(form["score"])).where(
            Video.ytid == form["ytid"]
        ).execute()

        return Response("ok")


class ModifyIgnoreTerms(HTTPEndpoint):
    def get(self, request):
        terms = IgnoreTerm.select()
        return render_to_response(
            "ModifyIgnoreTerms.html", dict(terms=terms, BRAND_NAME=BRAND_NAME)
        )

    async def post(self, request: Request):
        form = await request.form()
        add_term = form.get("add_term", "").strip()
        if add_term:
            IgnoreTerm.create(term=add_term)
        delete_term_id = form.get("delete_term_id")
        if delete_term_id:
            IgnoreTerm.delete_by_id(int(delete_term_id))
        return RedirectResponse(request.url, status_code=303)

    async def delete(self, request: Request):
        form = await request.form()
        term = form["term"]
        assert term
        IgnoreTerm.delete_by_id(term=term)
        return Response(f"<li>{term}</li>")


class WatchMPV(HTTPEndpoint):
    """
    VLC can rotate video with --video-filter='transform{type="90"}'
    but it makes the video choppy.
    also need to set COMSPEC because this only works in PowerShell

    """

    async def post(self, request: Request):
        form = await request.form()

        ytid = form.get("ytid")
        channel_id = form.get("channel_id")
        play_all = form.get("play_all")
        if not (ytid or channel_id or play_all):
            return HTMLResponse("Invalid request")
        if ytid:
            video = Video.get_by_id(ytid)
            video.local_view_count += 1
            video.save()
            channel = video.channel
            channel.local_view_count += 1
            channel.save()
            is_landscape = video.width > video.height
            paths = [video.file_path()]
        else:
            is_landscape = bool(form.get("rotated"))
            if channel_id:
                channel = Channel.get(id=channel_id)
                channel.local_view_count += 1
                channel.save()
            else:
                channel = None
            paths = get_downloaded_paths(is_landscape=is_landscape, channel=channel)

            if not paths:
                return HTMLResponse("no videos to play")
            random.shuffle(paths)

        # it seems that backslashes work but not as_posix()
        import shlex
        args = [common.VIDEO_PLAYER_CMD] + shlex.split(common.VIDEO_PLAYER_FLAGS)
        if 'mpv' in common.VIDEO_PLAYER_CMD and common.FORCE_VERTICAL and is_landscape:
            args.append("--video-rotate=90")
        args += [str(p) for p in paths]
        # use .Popen instead of .call so it doesn't block
        try:
            subprocess.Popen(args)
        except Exception as exc:
            print_function("ERROR: couldn't launch the video player:")
            print_function(repr(exc))
            print_function("Command was:")
            print_function(shlex.join(args))
            return HTMLResponse("<p>❌Error. Check the server logs</p>")

        return HTMLResponse("")


class ChannelAction(HTTPEndpoint):
    async def post(self, request: Request):
        form = await request.form()
        channel_id = form["channel_id"]
        channel: Channel = Channel.get(id=channel_id)
        action = form["action"]
        file_browser_commands = {
            "win32": "explorer",
            "darwin": "Finder",
        }
        file_browser_command = file_browser_commands.get(sys.platform, "xdg-open")

        if action == "file-browser-videos":
            subprocess.run([file_browser_command, channel.video_dir()])
            return HTMLResponse("launched file browser")
        if action == "file-browser-thumbnails":
            subprocess.run([file_browser_command, channel.thumbnail_dir()])
            return HTMLResponse("launched file browser")
        if action == "download-previews-chunk":
            schedule_download_previews_chunk(channel)
            return HTMLResponse(
                f"Downloading most recent {_PREVIEWS_CHUNK_SIZE} previews"
            )
        if action == "download-missing-thumbnails":
            # better to do it here in main process rather than worker,
            # because this is already async and we sidestep the issue with
            # WindowsSelectorEventLoopPolicy.
            ytids_with_thumbnails = set(
                p.stem for p in channel.thumbnail_dir().glob("*.jpg")
            )
            ytids = []
            for v in Video.select(Video.ytid).where(Video.channel == channel):
                if v.ytid not in ytids_with_thumbnails:
                    ytids.append(v.ytid)
                    # get the full object
            task = BackgroundTask(
                download_video_thumbnails, channel_id=channel.id, ytids=ytids
            )
            return HTMLResponse(
                "Downloading thumbnails. Wait a bit then reload.", background=task
            )


class DeleteChannel(HTTPEndpoint):
    async def post(self, request: Request):
        form = await request.form()
        channel_id = form["channel_id"]
        channel: Channel = Channel.get(id=channel_id)
        files = list(channel.local_video_paths())
        if files:
            return HTMLResponse(
                "Error: Before deleting the channel, you must delete all videos in the folder."
            )
        channel.delete_instance()
        return RedirectResponse(app.router.url_path_for("Index"), status_code=303)


_PREVIEWS_CHUNK_SIZE = 50


def schedule_download_previews_chunk(channel: Channel):
    num_scheduled = 0
    for v in channel.videos():
        # part of downloading the preview is also fetching video dims
        # which is necessary for displaying the preview properly.
        # the user might delete the channel from the DB but keep the preview viedos
        # so we must re-download the stats.
        if not (v.preview_file_path().exists() and v.height):
            v.schedule_download_preview()
            num_scheduled += 1
        # each time you toggle it, download another chunk
        if num_scheduled > _PREVIEWS_CHUNK_SIZE:
            return


class ToggleAutoDownloadPreview(HTTPEndpoint):
    async def post(self, request: Request):
        form = await request.form()
        channel_id = form["channel_id"]
        channel: Channel = Channel.get(id=channel_id)
        channel.auto_download_previews = not channel.auto_download_previews
        channel.save()
        # download previous videos
        if channel.auto_download_previews:
            schedule_download_previews_chunk(channel)
        return RedirectResponse(channel.local_url(), status_code=303)


SHOW_STATIC_THUMBNAILS_COOKIE = "show_static_thumbnails"
SEARCH_ORDER_BY_DATE_COOKIE = "search_order_by_date"


def get_show_static_thumbnails(request: Request):
    return request.cookies.get(SHOW_STATIC_THUMBNAILS_COOKIE)


class ToggleShowStaticThumbnails(HTTPEndpoint):
    async def post(self, request: Request):
        form = await request.form()
        channel_id = form["channel_id"]
        channel: Channel = Channel.get(id=channel_id)
        response = RedirectResponse(channel.local_url(), status_code=303)

        current_setting = get_show_static_thumbnails(request)
        new_setting = "" if current_setting else "1"
        response.set_cookie(SHOW_STATIC_THUMBNAILS_COOKIE, new_setting)

        return response


static_app = StaticFiles(directory=FILES_ROOT, packages=[__name__])
app = Starlette(
    debug=True,
    routes=[
        Route("/", Index, name="Index"),
        Route("/channel/{channel_id}", BrowseChannel, name="BrowseChannel"),
        Route("/search", Search),
        Route("/UpdateFromYouTube", UpdateFromYouTube, name="UpdateFromYouTube"),
        Route("/RecentlyPublished", RecentlyPublished, name="RecentlyPublished"),
        Route("/Downloads", Downloads, name="Downloads"),
        Route("/AddChannel", AddChannel, name="AddChannel"),
        Route("/download", Download),
        Route("/ignore_terms", ModifyIgnoreTerms),
        Route("/change_score", ChangeScore),
        Route("/mpv", WatchMPV, name="WatchMPV"),
        Route("/channel-action", ChannelAction),
        Route("/delete-channel", DeleteChannel, name="DeleteChannel"),
        Route(
            "/ToggleAutoDownloadPreview",
            ToggleAutoDownloadPreview,
            name="ToggleAutoDownloadPreview",
        ),
        Route(
            "/ToggleShowStaticThumbnails",
            ToggleShowStaticThumbnails,
            name="ToggleShowStaticThumbnails",
        ),
        Mount(
            "/static",
            app=static_app,
            name="static",
        ),
    ],
)


def runserver(port):
    # so that we don't log every thumbnail load
    # but this is not working?
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.setLevel("WARNING")

    import uvicorn

    # don'w want to reload in the middle of fetching a huge channel
    # from youtube
    if os.getenv("YTCL_DEV"):
        reload_kwargs = dict(
            reload=True,
            reload_dirs=[Path(__file__).parent],
        )
    else:
        reload_kwargs = dict(
            reload=False,
        )

    uvicorn.run(
        f"{__name__}:app",
        port=port,
        **reload_kwargs,
        # Don't write access log because we get tons of output
        # e.g. loading thumbnails
        access_log=False,

    )


def main():
    parser = argparse.ArgumentParser(description=CMD_NAME)
    parser.add_argument(
        'cmd',
        nargs='?',
        choices=[
            SUBCOMMANDS.CREATE,
            #SUBCOMMANDS.WEB,
            # need this because the subprocess uses it
            SUBCOMMANDS.WORKER,
            #SUBCOMMANDS.ALL,
            SUBCOMMANDS.HELP,
        ],
        #default=SUBCOMMANDS.ALL,
    )

    parser.add_argument(
        '--port',
        type=int,
        default=common.PORT,
    )

    args = parser.parse_args()
    cmd = args.cmd or SUBCOMMANDS.ALL

    if cmd == SUBCOMMANDS.HELP:
        print_function(_MSG_HELP)
        sys.exit(0)

    if cmd == SUBCOMMANDS.CREATE:
        common.create_library()
        sys.exit(0)

    common.startup_checks()
    db.create_tables([Channel, Video, IgnoreTerm, QueuedTask])

    if cmd == SUBCOMMANDS.WORKER:
        from .tasks import listen
        listen()

    if cmd == SUBCOMMANDS.ALL:
        # don't want to run on a different port every time,
        # because it's not like pictriage where you launch it for a specific task.
        # it's something you can keep running for days.
        subprocess.Popen(
            [CMD_NAME, SUBCOMMANDS.WORKER]
        )

        if common.LAUNCH_BROWSER:
            import webbrowser

            webbrowser.open(f"http://127.0.0.1:{args.port}")
        runserver(args.port)



_MSG_HELP = f"""
"{CMD_NAME} create": create a {BRAND_NAME} library in the current dir
"{CMD_NAME}": launch the {BRAND_NAME} server
"{CMD_NAME} worker": launch the {BRAND_NAME} worker process (necessary for downloading videos)
"{CMD_NAME} all": launch server+worker together.
"""



if __name__ == "__main__":
    main()
