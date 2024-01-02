# YTVIP: a yt-dlp GUI & archive

YTVIP is a local YouTube video library. Add your favorite channels,
then browse, search, and download available videos, 
see which ones you have already downloaded,
and of course watch them.

It's a friendlier and more visual alternative to browsing folders of videos on your desktop.

![Screenshot](demo.gif)

## Why use this?

There are various YouTube channel downloaders/archives out there.
The advantage of YTVIP is that it's simple, minimal, and lightweight:

-	Can be installed simply with `pip install ytvip`. 
	(No need for Docker, Node.js, a database server, or any other complex setup.)
-	Lightweight with low system requirements. 
	(Unlike some other programs that require gigabytes of RAM.)
-	Small and simple codebase, written in Python, HTML, and some vanilla JavaScript.
	(Some tools end up abandoned by their author and then nobody wants to take over
	because they don't really understand the code or how to get it to compile.)
	

## Features:

- Fast
- Clean, minimal interface
- Works offline; only connects to YouTube when you ask it to update/download videos.
- Can add videos to your library that you already downloaded some other way (see below) 
- Better search experience than YouTube UI in many ways:
  - search your whole library, including filtering by multiple channels
    (not possible through regular YouTube UI)
  - search is instantaneous (works locally)
  - no "recommended for you" or other distractions
- Videos play in your favorite media player (configurable in `settings.toml`)
- Small codebase with few dependencies

## Quickstart

### Prerequisites

-	Python
-	YouTube API key
-	ffmpeg (must be on `PATH`)
-	VLC to watch videos, or can set a different program such as mpv in `settings.toml`.
	Video player must be on `PATH`.
	

### Setup

```commandline
pip install ytvip 
mkdir travel-vlogs
cd travel-vlogs
ytvip create

```

Open `settings.toml` and fill in your API key.

### Running

Just run: `ytvip`

## Moving pre-existing videos into YTVIP

To move your existing videos into YTVIP,
just rename them according to their YouTube video ID,
e.g. `y4bqhRGchmU.mp4`,
and drop them into the proper channel subfolder. 
