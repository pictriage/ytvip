import asyncio
import atexit
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import aiohttp
import toml
import yt_dlp
from aiohttp import ClientSession
from icecream import ic  # noqa

print_function = print

FILES_ROOT = Path('.')
BRAND_NAME = 'YTVIP'

CMD_NAME = BRAND_NAME.lower()

VIDEOS_ROOT = FILES_ROOT.joinpath('videos')
THUMBNAILS_ROOT = FILES_ROOT.joinpath('thumbnails')
CHANNEL_THUMBNAILS_DIR = FILES_ROOT.joinpath('channel_thumbnails')

PREVIEW_ROOT = FILES_ROOT.joinpath('preview_videos')
PREVIEW_SHORT_ROOT = FILES_ROOT.joinpath('preview_videos_shorter')

# call it this so it doesn't get confused with some other settings.json
# and easier to search for and more distinctive.
# but actually since it's toml the name will be more distinctive,
# and we can change the product name later.
_PREFS_FILE = FILES_ROOT.joinpath(f'settings.toml')



# should initialize these settings at module scope rather than inside the setup()
# function, because they are immediately imported into other modules,
# so by the time the setup() function patches them, it's too late.
if _PREFS_FILE.exists():
    _prefs = toml.loads(_PREFS_FILE.read_text('utf8'))
else:
    _prefs = {}

class _PREFKEYS:
    YOUTUBE_API_KEY = 'youtube_api_key'
    YT_DLP_EXECUTABLE = 'yt_dlp_executable'
    YT_DLP_FLAGS = 'yt_dlp_flags'
    VIDEO_PLAYER_COMMAND = 'video_player_command'
    VIDEO_PLAYER_EXECUTABLE = 'video_player_executable'
    VIDEO_PLAYER_FLAGS = 'video_player_flags'
    FORCE_VERTICAL = 'force_vertical'
    PORT = 'port'
    RECENT_DAYS = 'recent_days'
    LAUNCH_BROWSER = 'launch_browser'

DEFAULT_PORT = 8500
DEFAULT_VIDEO_PLAYER_COMMAND = 'vlc'

_INITIAL_PREFS_CONTENT = {
    _PREFKEYS.YOUTUBE_API_KEY: '',
    _PREFKEYS.PORT: DEFAULT_PORT,
    _PREFKEYS.YT_DLP_FLAGS: '',
    _PREFKEYS.VIDEO_PLAYER_EXECUTABLE: DEFAULT_VIDEO_PLAYER_COMMAND,
}

LAUNCH_BROWSER = _prefs.get(_PREFKEYS.LAUNCH_BROWSER, True)
YOUTUBE_API_KEY = _prefs.get(_PREFKEYS.YOUTUBE_API_KEY)
YT_DLP_CMD = (
        _prefs.get(_PREFKEYS.YT_DLP_EXECUTABLE) or
        'yt-dlp'
)
YT_DLP_FLAGS = _prefs.get(_PREFKEYS.YT_DLP_FLAGS, '')
VIDEO_PLAYER_CMD = (
        _prefs.get(_PREFKEYS.VIDEO_PLAYER_EXECUTABLE) or
        _prefs.get(_PREFKEYS.VIDEO_PLAYER_COMMAND) or
        DEFAULT_VIDEO_PLAYER_COMMAND
)

if _prefs.get(
    _PREFKEYS.VIDEO_PLAYER_FLAGS):
    VIDEO_PLAYER_FLAGS = _prefs[_PREFKEYS.VIDEO_PLAYER_FLAGS]
elif 'mpv' in VIDEO_PLAYER_CMD:
    VIDEO_PLAYER_FLAGS = "--fullscreen --ontop"
elif 'vlc' in VIDEO_PLAYER_CMD:
    VIDEO_PLAYER_FLAGS = '--video-on-top --fullscreen'
else:
    VIDEO_PLAYER_FLAGS = ''


FORCE_VERTICAL = _prefs.get(_PREFKEYS.FORCE_VERTICAL)
PORT = _prefs.get(_PREFKEYS.PORT, DEFAULT_PORT)
RECENT_DAYS = _prefs.get(_PREFKEYS.RECENT_DAYS, 30)


_ytids_to_ignore_file = Path('ytids_to_ignore.txt')
if _ytids_to_ignore_file.exists():
    YTIDS_TO_IGNORE = set(_ytids_to_ignore_file.read_text('utf8').split())
    YTIDS_TO_IGNORE.discard('')
else:
    YTIDS_TO_IGNORE = None

_highlight_terms_file = Path('terms_to_highlight.txt')
if _highlight_terms_file.exists():
    TERMS_TO_HIGHLIGHT = set(_highlight_terms_file.read_text('utf8').split())
    TERMS_TO_HIGHLIGHT.discard('')
else:
    TERMS_TO_HIGHLIGHT = None


class SUBCOMMANDS:
    CREATE = 'create'
    WEB = 'web'
    WORKER = 'worker'
    ALL = 'all'
    HELP = 'help'



_MSG_NOT_LIBRARY_FOLDER = f"""
This does not appear to be a {BRAND_NAME} folder ({_PREFS_FILE} is missing). 
To create a {BRAND_NAME} video library here, run "{CMD_NAME} {SUBCOMMANDS.CREATE}".
"""

_MSG_YOUTUBE_API_KEY = f"""
Sign up for a YouTube API key and store it in the file "{_PREFS_FILE}" in this folder.
"""

_MSG_START_SERVER = f"""
Run these commands:
'{CMD_NAME} {SUBCOMMANDS.WEB}' to start the web interface
'{CMD_NAME} {SUBCOMMANDS.WORKER}' to start the worker process

or: '{CMD_NAME}' to start the web & worker together (experimental)
"""

def create_library():

    VIDEOS_ROOT.mkdir(exist_ok=True)
    THUMBNAILS_ROOT.mkdir(exist_ok=True)
    CHANNEL_THUMBNAILS_DIR.mkdir(exist_ok=True)
    PREVIEW_ROOT.mkdir(exist_ok=True)
    PREVIEW_SHORT_ROOT.mkdir(exist_ok=True)
    _PREFS_FILE.write_text(
        toml.dumps(_INITIAL_PREFS_CONTENT), encoding='utf8'
    )
    print_function(f"Created a {BRAND_NAME} library! :)")
    print_function(_MSG_YOUTUBE_API_KEY)
    if shutil.which('mpv') is None:
        print_function(
            "Also, it is highly recommended to install MPV (and add it to your PATH)."
        )
    print_function(_MSG_START_SERVER)
    sys.exit(0)


def startup_checks():

    if not _PREFS_FILE.exists():
        sys.exit(_MSG_NOT_LIBRARY_FOLDER)

    if not YOUTUBE_API_KEY:
        sys.exit(_MSG_YOUTUBE_API_KEY)


# don't want to have any partially downloaded or corrupted files in the output dir.
# if storing files on an external hard drive or flash drive,
# it's faster to use a temp dir because writes are faster.

TEMP_DIR = Path(tempfile.mkdtemp())


def exit_handler():
    shutil.rmtree(TEMP_DIR)


atexit.register(exit_handler)


def call(*segments, capture_output=False):
    """Remember to use shlex.quote for any file paths that can contain spaces"""
    cmd_str = ' '.join(str(arg) for arg in segments)
    print_function(cmd_str)
    # cmd = cmd_str.split()
    import shlex

    # use shlex.split so that it is smart about quoted things like
    # filenames and ffmpeg -vf filter
    cmd = shlex.split(cmd_str)
    try:
        return subprocess.run(cmd, capture_output=capture_output, check=True)
    except subprocess.CalledProcessError as exc:
        print_function('Command failed:')
        print_function(cmd)
        print_function(exc.stderr)
        raise


def path2url(path: Path):
    assert path.exists()
    relpath = path.relative_to(FILES_ROOT).as_posix()
    return f'/static/{relpath}'


def download_video_file_info(ytid):
    watch_url = f'https://www.youtube.com/watch?v={ytid}'
    with yt_dlp.YoutubeDL({}) as ydl:
        try:
            d2 = ydl.extract_info(watch_url, download=False)
        except yt_dlp.utils.DownloadError:
            print_function('Download error', watch_url)
            return

    format_keys = ['width', 'height', 'fps', 'format_id']

    format = {k: d2['formats'][-1][k] for k in format_keys}
    width = int(format['width'])
    height = int(format['height'])

    return dict(
        width=width,
        height=height,
        fps=int(format['fps']),
    )


async def download_video_thumbnail(ytid, outpath: Path, session: ClientSession):
    """
    I think we need thumbnails no matter what....for example to show
    the channel thumbnail.
    """
    image_versions = ['maxresdefault', 'hqdefault', 'hqdefault']
    for version in image_versions:
        url = f'https://i.ytimg.com/vi/{ytid}/{version}.jpg'
        try:
            async with session.get(url, timeout=10) as response:
                content = await response.read()
                outpath.write_bytes(content)
                return
        except (aiohttp.ServerDisconnectedError, asyncio.TimeoutError) as exc:
            print_function(exc)
            print_function(f"Couldn't download thumbnail for {ytid}, skipping")
            return

        # except aiohttp.
        # except urllib.error.HTTPError:
        #     # if maxresdefault is missing it will just be a small
        #     # gray placeholder that is 1kb
        #     # and the status code will be 404
        #     pass


async def download_video_thumbnails(channel_id, ytids: list):
    tasks = []

    folder = channel_thumbnail_dir(channel_id)
    async with ClientSession() as session:
        for ytid in ytids:
            outpath = thumbnail_path(folder, ytid)
            if not outpath.exists():
                task = download_video_thumbnail(ytid, outpath, session)
                tasks.append(task)

        _ = await asyncio.gather(*tasks)


def channel_thumbnail_dir(channel_id):
    return THUMBNAILS_ROOT.joinpath(channel_id)


def thumbnail_path(channel_dir: Path, ytid):
    return channel_dir.joinpath(f'{ytid}.jpg')


def convert_iso8601(s):
    """
    Converts YouTube duration (ISO 8061)
    into Seconds

    see http://en.wikipedia.org/wiki/ISO_8601#Durations
    """
    regex = re.compile(
        "P"  # designates a period
        "(?:(?P<years>\d+)Y)?"  # years
        "(?:(?P<months>\d+)M)?"  # months
        "(?:(?P<weeks>\d+)W)?"  # weeks
        "(?:(?P<days>\d+)D)?"  # days
        "(?:T"  # time part must begin with a T
        "(?:(?P<hours>\d+)H)?"  # hours
        "(?:(?P<minutes>\d+)M)?"  # minutes
        "(?:(?P<seconds>\d+)S)?"  # seconds
        ")?"
    )  # end of time part
    # Convert regex matches into a short list of time units
    units = regex.match(s).groups()[-3:]
    hours, minutes, seconds = [int(x) if x != None else 0 for x in units]
    return 3600 * hours + 60 * minutes + seconds
