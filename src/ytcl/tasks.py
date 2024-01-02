import logging
import json
import time
from pathlib import Path
from ytcl.common import download_video_file_info, print_function, YT_DLP_CMD, TEMP_DIR

from .common import call, YT_DLP_CMD, TEMP_DIR, YT_DLP_FLAGS
from .models import Video, DOWNLOAD_STATUS, QueuedTask
from icecream import ic  # noqa

print_function = print


logger = logging.getLogger(__name__)

def listen():
    print_function("Worker is listening for messages")

    while True:
        task: QueuedTask = QueuedTask.select().order_by(QueuedTask.priority.desc()).first()
        if not task:
            time.sleep(5)
            continue
        operation = task.operation
        kwargs = json.loads(task.kwargs_json)
        fxns = dict(download=download, download_preview=download_preview)
        fxn = fxns[operation]
        try:
            fxn(**kwargs)
        except Exception as exc:
            logger.exception(repr(exc))
        task.delete_instance()


def download(ytid, channel_dir: str, preview_channel_dir: str):
    channel_dir = Path(channel_dir)
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

    download_preview(ytid, preview_channel_dir)
    print_function(f"Downloaded {ytid}: video and preview")


def download_preview(ytid, channel_dir: str, ss=5, to=25):
    channel_dir = Path(channel_dir)

    # download format stats because we need this in order to display
    # the preview clip with proper orientation.
    format_stats = download_video_file_info(ytid=ytid)

    if not format_stats:
        print_function(f"ERROR: cannot get info about {ytid}, skipping")
        return

    from .models import Video

    Video.update(**format_stats).where(Video.ytid == ytid).execute()

    call(
        YT_DLP_CMD,
        f'https://www.youtube.com/watch?v={ytid}',
        '--output',
        f"{ytid}.%(ext)s",
        f"""--downloader ffmpeg --downloader-args "ffmpeg_i:-ss {ss} -to {to}" """,
        """ -S "res:360" """,
        """ -f "bv" """,
        '--paths',
        f"temp:{TEMP_DIR.as_posix()}",
        '--paths',
        f"home:{channel_dir.as_posix()}",
    )
