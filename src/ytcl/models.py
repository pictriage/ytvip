import shlex
import time
import typing
import urllib.error
from pathlib import Path
from typing import List
from urllib.request import urlopen

from icecream import ic  # noqa
from peewee import *

from . import common
from . import youtube_api
from .common import (
    VIDEOS_ROOT,
    FILES_ROOT,
    PREVIEW_ROOT,
    path2url,
    call,
)

# apparently peewee doesn't work well with async ;(
# https://fastapi.tiangolo.com/how-to/sql-databases-peewee/
# maybe that is the root of my problems?
# i'm getting an error on Linux.
# it's triggered by db.connect(). Whichever process does it second fails.
# peewee.OperationalError: locking protocol
# not fixed by using check_same_thread=False
# i think it's this: https://stackoverflow.com/a/46347618
# but how is it related? i don't know what the subprocess is.
# maybe uvicorn always launches a subprocess?
# the error happens whether i use wal or not.
db = SqliteDatabase('db.sqlite3', pragmas={
    'foreign_keys': 1,
    #'journal_mode': 'wal'
}, check_same_thread=False)

VIDEO_FILE_EXTENSIONS = ['webm', 'mp4', 'mkv', 'avi']

print_function = print

def now_unix():
    return int(time.time())


class Channel(Model):
    name = CharField(unique=True)
    id = CharField(unique=True, primary_key=True)
    thumbnail_url = CharField()
    local_view_count = IntegerField(default=0)
    auto_download_previews = BooleanField(default=False)

    # useful to have especially since id column is not sequential.
    timestamp_added = IntegerField(default=now_unix)

    def __str__(self):
        return f'<Channel: {self.name}, {self.id}>'

    class Meta:
        database = db  # This model uses the "people.db" database.

    def local_url(self):
        from . import app

        return app.router.url_path_for('BrowseChannel', channel_id=self.id)

    def populate_videos_url(self):
        from . import app

        return app.router.url_path_for('UpdateFromYouTube') + '?channel_id=' + self.id

    def youtube_url(self):
        return f'https://www.youtube.com/channel/{self.id}'

    def video_dir(self) -> Path:
        return VIDEOS_ROOT.joinpath(self.id)

    def preview_video_dir(self) -> Path:
        return common.PREVIEW_ROOT.joinpath(self.id)

    def preview_video_dir_shorter(self) -> Path:
        return common.PREVIEW_SHORT_ROOT.joinpath(self.id)

    def thumbnail_dir(self) -> Path:
        return common.channel_thumbnail_dir(self.id)

    def download_thumbnail(self):
        outpath = self.thumbnail_file_path()
        if outpath.exists():
            return
        try:
            with urlopen(self.thumbnail_url) as resp:
                content = resp.read()
                outpath.write_bytes(content)
        except urllib.error.HTTPError as exc:
            print_function("Couldn't get thumbnail for", self)
            print_function(exc)
            pass

    def thumbnail_file_path(self):
        return FILES_ROOT.joinpath(self.thumbnail_static_path())

    def thumbnail_static_path(self):
        return f'channel_thumbnails/{self.id}.jpg'

    def videos(
        self,
        where: typing.Optional[list] = None,
        order_by: typing.Optional[list] = None,
    ) -> typing.List['Video']:

        if not where:
            where = []
        if not order_by:
            order_by = []

        where.append(Video.channel == self)

        # is it ok if there are redundant or contradictory sorts on the same field?
        order_by.append(Video.published_at.desc())

        qs = Video.select().where(*where).order_by(*order_by)

        # force to a list so i don't mistakenly try to use a second order_by etc.
        return list(qs)

    def num_videos(self):
        return Video.select().where(Video.channel == self).count()

    def urls_of_preview_videos(self):
        viewed_videos = self.videos(
            where=[Video.local_view_count > 0], order_by=[Video.local_view_count.desc()]
        )
        for v in viewed_videos:
            fp = v.preview_file_path()
            if fp.exists():
                yield v
        for i, fp in enumerate(self.preview_video_paths()):
            v = Video.get(ytid=fp.stem)
            yield v

    def update_from_api(self):
        data = youtube_api.get_channel_api_data(self.youtube_url())
        del data['id']
        for k, v in data.items():
            setattr(self, k, v)
        self.save()

    def local_video_paths(self):
        video_path = self.video_dir()
        if not video_path.exists():
            return
        for p in video_path.iterdir():
            if p.suffix[1:] in VIDEO_FILE_EXTENSIONS:
                yield p

    def preview_video_paths(self):
        # self.preview_video_dir_reencoded()
        for video_path in [self.preview_video_dir(), self.preview_video_dir_shorter()]:
            if not video_path.exists():
                continue
            for p in video_path.iterdir():
                if p.suffix[1:] in VIDEO_FILE_EXTENSIONS:
                    yield p

    # def get_videos_by_orientation(self, is_landscape, local_only=False) -> List['Video']:
    #     all_videos = Video.select().where(Video.width > Video.height == is_landscape)
    #     if local_only:
    #         downloaded_ytids = set(p.stem for p in self.local_video_paths())
    #         return [v for v in all_videos if v.ytid in downloaded_ytids]
    #     return list(all_videos)

    def num_local_videos(self):
        return len(list(self.local_video_paths()))

    # @classmethod
    # def ranked_by_local_views(cls):
    #     channels = (cls
    #              .select(cls, fn.SUM(Video.local_view_count).alias('local_view_count'))
    #              .join(Video, JOIN.LEFT_OUTER)
    #              .group_by(cls))
    #     for c in channels:


class IgnoreTerm(Model):
    class Meta:
        database = db

    term = TextField()

    @classmethod
    def all_terms(cls) -> set:
        return set(t.term for t in cls.select())

class DOWNLOAD_STATUS:
    QUEUED = 'queued'
    STALE = '?'
    FAILED = 'failed'
    DOWNLOADED = 'downloaded'


class Video(Model):
    class Meta:
        database = db

    # I added the on_delete=cascade after the DB was created,
    # so this will only apply to newly created projects.
    # i also added the pragma later.
    channel: Channel = ForeignKeyField(Channel, on_delete='CASCADE')
    ytid = CharField(unique=True, primary_key=True)
    title = TextField()
    published_at = DateTimeField()
    duration = IntegerField()

    # we might run yt-dlp later since it is slow
    width = IntegerField(null=True)
    height = IntegerField(null=True)
    fps = IntegerField(null=True)
    yt_view_count = IntegerField()
    yt_like_count = IntegerField(null=True)
    score = IntegerField(default=0)
    local_view_count = IntegerField(default=0)
    # could be 'failed', 'in progress', or maybe others
    download_status = CharField(default='')
    download_status_epoch = IntegerField(null=True)
    # useful to have especially since id column is not sequential.
    added_locally_epoch = IntegerField(default=now_unix)

    def set_download_status(self, status):
        self.download_status = status
        self.download_status_epoch = int(time.time())
        self.save()

    def download_status_for_dl_button(self):
        """
        only consider it queued for an hour or two...
        if it still hasn't been downloaded by then,
        maybe it was de-queued (e.g. huey DB was deleted or the download failed)
        """
        status = self.download_status
        if status == DOWNLOAD_STATUS.QUEUED:
            dt = self.download_status_epoch
            NUM_HOURS = 2
            if dt and dt > time.time() - NUM_HOURS * 60 * 60:
                return DOWNLOAD_STATUS.QUEUED
            return f"Queued >{NUM_HOURS}hrs"
        if status == DOWNLOAD_STATUS.DOWNLOADED:
            # means it must be missing
            return ''
        return status

    def thumbnail_path(self) -> Path:
        return common.thumbnail_path(self.channel.thumbnail_dir(), self.ytid)

    def views_per_like(self):
        yt_like_count = self.yt_like_count or 1
        return self.yt_view_count / yt_like_count

    def file_path(self):
        path = None
        video_dir = self.channel.video_dir()
        for ext in VIDEO_FILE_EXTENSIONS:
            path = video_dir.joinpath(f'{self.ytid}.{ext}')
            if path.exists():
                return path
        return path

    def is_recent(self):
        return self.published_at.timestamp() > time.time() - common.RECENT_DAYS * 24 * 60 * 60

    def should_rotate(self):
        return common.FORCE_VERTICAL and self.height and (self.width > self.height)

    def horz_vert_htov(self):
        h = self.height
        w = self.width


        # this usually shouldn't happen. if there is a preview clip,
        # then we should also have the video dims,
        # unless that failed for some reason.
        # or if you deleted the channel but kept the preview videos.
        # in this case it will skip re-downloading the preview,
        # which is where the stats also get downloaded.
        if not h:
            return 'horz'

        if h > w:
            return 'vert'

        if common.FORCE_VERTICAL:
            return 'htov'
        return 'horz'


    def display_orientation(self):
        if self.horz_vert_htov() == 'horz':
            return 'horz'
        return 'vert'


    def is_1080p_or_lower(self):
        return max(self.width, self.height) < 2000

    def preview_file_path(self):
        path = None
        for video_dir in [
            self.channel.preview_video_dir(),
            self.channel.preview_video_dir_shorter(),
        ]:
            for ext in VIDEO_FILE_EXTENSIONS:
                path = video_dir.joinpath(f'{self.ytid}.{ext}')
                if path.exists():
                    return path
        return path

    def preview_url(self):
        return path2url(self.preview_file_path())

    # def schedule_appropriate_preview(self):
    #     if self.channel.auto_download_previews:
    #         self.schedule_download_preview()
    #     else:
    #         # even though it doesn't take long, we don't want to do anything
    #         # that can slow down loading of new videos
    #         self.schedule_download_preview_shorter()

    def schedule_download_preview(self):
        from . import tasks

        tasks.download_preview(self.ytid, self.channel.preview_video_dir())

    def schedule_download_preview_shorter(self):
        from . import tasks

        tasks.download_preview(self.ytid, self.channel.preview_video_dir(), ss=0, to=1)

    def download_preview_shorter(self):
        common.download_preview_immediate(
            self.ytid, self.channel.preview_video_dir_shorter(), ss=0, to=1
        )

    def reencode_preview(self):
        """
        todo: download to a temp dir
        even though we can download from youtube, can't necessarily get rid of this
        yet, because some videos have been deleted from youtube.
        however, theoretically we won't need this if we download preview versions
        before we down the full-size version. and i already re-encoded the legacy
        downloaded videos
        """
        inp = self.file_path()
        outdir = self.channel.preview_video_dir()
        if not outdir.is_dir():
            outdir.mkdir()
        outp = outdir.joinpath(inp.name)

        # don't rotate because we want to be consistent with preview-version videos
        # downloaded from youtube. the rotating happens in HTML.
        call(
            'ffmpeg -ss 5 -t 20',
            '-i',
            shlex.quote(inp.as_posix()),
            f'-vf "scale=360:-1" -crf 25 -y',
            shlex.quote(outp.as_posix()),
        )

    def preview_height(self):
        w = self.width
        h = self.height
        if w > h:
            w, h = h, w
        return int(216 * h / w)


def get_downloaded_paths(orientation=None, channel=None) -> List[Path]:

    qs = Video.select()
    if orientation == 'horz':
        qs = qs.where(Video.width > Video.height)
    elif orientation == 'vert':
        qs = qs.where(Video.width < Video.height)
    if channel:
        qs = qs.where(Video.channel == channel)
    oriented_ytids = set(v.ytid for v in qs)
    paths = []
    for path in get_all_downloaded_paths():
        if path.stem in oriented_ytids:
            paths.append(path)
    return paths


def get_all_downloaded_paths():
    paths = []
    for ext in VIDEO_FILE_EXTENSIONS:
        for path in VIDEOS_ROOT.glob(f'**/*.{ext}'):
            paths.append(path)
    return paths

def get_all_preview_paths():
    paths = []
    for ext in VIDEO_FILE_EXTENSIONS:
        for path in PREVIEW_ROOT.glob(f'**/*.{ext}'):
            paths.append(path)
    return paths


db.connect()


def migrate():
    from playhouse.migrate import SqliteMigrator, migrate

    cur = db.cursor()
    cur.execute("PRAGMA user_version")
    user_version = cur.fetchall()[0][0]

    migrator = SqliteMigrator(db)

    if user_version == 0:
        pass
    elif user_version < 2:
        migrate(
            migrator.add_column('video', 'download_status', Video.download_status)
        )
        migrate(
            migrator.rename_column('video', 'download_requested_epoch', 'download_status_epoch')
        )
        migrate(
            migrator.rename_column('video', 'timestamp_added_locally', 'added_locally_epoch')
        )

    new_user_version = 2
    if user_version < new_user_version:
        cur.execute(f"PRAGMA user_version = {new_user_version}")

migrate()