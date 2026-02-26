import os

username = "vinal-2"
repo     = "video_agent"
branch   = "full-stack"
folder   = "scripts"

for f in os.listdir(folder):
    raw_url = f"https://raw.githubusercontent.com/vinal-2/video_agent//refs/heads/full-stack/scripts"
    print(raw_url)
