#!/bin/bash
#python3 -m pip install requests

#python3 YouTubeLinkGrabber.py > ./youtube.m3u

python cookies.py
python generate.py

echo M3U8 And Cookies grabbed.
