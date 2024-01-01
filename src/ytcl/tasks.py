from pathlib import Path
from huey import SqliteHuey
from .common import call, YT_DLP_CMD, TEMP_DIR, download_preview_immediate, YT_DLP_FLAGS
from .models import Video, DOWNLOAD_STATUS
from icecream import ic  # noqa

print_function = print

huey = SqliteHuey(filename='huey.sqlite3')


# sometimes huey doesn't do anything until I press Ctrl+C
# apparently this is an issue with greenlets.
# it doesn't even seem to be doing anything in parallel


@huey.task()
def download(ytid, channel_dir: Path, preview_channel_dir: Path):
    # youtube seems to be blocking me and returning 403 partway through
    # the download:
    # https://github.com/yt-dlp/yt-dlp/issues/7860
    # but when I run it from the CLI it works fine!
    # a video that gets 403'd instantly through the API
    # then downloads fine through the CLI

    # yes this works! i don't get the download limit.

    try:
        call(
            YT_DLP_CMD,
            YT_DLP_FLAGS,
            f'https://www.youtube.com/watch?v={ytid}',
            '--output',
            f"{ytid}.%(ext)s",
            '--paths',
            f"temp:{TEMP_DIR.as_posix()}",
            '--paths',
            f"home:{channel_dir.as_posix()}",
        )
    except Exception as exc:
        video = Video.get(ytid=ytid)
        video.set_download_status(DOWNLOAD_STATUS.FAILED)
        video.save()
        raise
    video = Video.get(ytid=ytid)
    video.set_download_status(DOWNLOAD_STATUS.DOWNLOADED)
    video.save()

    download_preview_immediate(ytid, preview_channel_dir)
    print_function(f"Downloaded {ytid}: video and preview")


# using this instead of huey.immediate because it seems immediate mode
# doesn't bubble exceptions. it's not the same as regular sync code.
download_preview = huey.task(priority=3)(download_preview_immediate)


@huey.task()
def reencode_preview(ytid):
    video = Video.get(ytid=ytid)
    video.reencode_preview()
