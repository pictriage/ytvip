# YTVIP: a personal YouTube video library for desktop

YTVIP is a local YouTube video library. Add your favorite channels,
then browse, search, and download available videos, 
see which ones you have already downloaded,
and of course watch them.

It's a friendlier and more visual alternative to browsing folders of videos on your desktop.

![Screenshot](demo.gif)

## Features:

- Fast
- Easy to install (doesn't require Docker or any complex setup)
- Clean, minimal interface
- Works offline; only connects to YouTube when you ask it to update/download videos.
- Can add videos to your library that you already downloaded some other way (see below) 
- Better search experience than YouTube UI in many ways:
  - search your whole library, including filtering by multiple channels
    (not possible through regular YouTube UI)
  - search is instantaneous (works locally)
  - no "recommended for you" or other distractions
- Video previews
- Auto-hide videos based on title words or other criteria
- Small codebase with few dependencies

## Quickstart

### Prerequisites
- Python
- YouTube API key
- ffmpeg
- mpv is required to watch videos 

### Setup

```commandline
pip install ytvip 
mkdir travel-vlogs
cd travel-vlogs
ytvip create 
```

Open `settings.toml` and fill in your API key.

### Running

On Windows: run `ytvip web`, open a separate terminal window and run `ytvip worker`.
The worker process is required to download videos.

On Mac/Linux: you can launch both processes with the `ytvip` command.
(This may work on Windows also but is experimental.)

## Moving pre-existing videos into YTVIP

To move your existing videos into YTVIP,
just rename them according to their YouTube video ID,
e.g. `y4bqhRGchmU.mp4`,
and drop them into the proper channel subfolder. 
