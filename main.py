import os, json, time, tempfile, subprocess, requests, random, textwrap
import praw
import google.generativeai as genai
import edge_tts
import asyncio
from PIL import Image, ImageDraw, ImageFont
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# -------------------------------------------------------------------
# 1. Retrieve secrets from environment variables (set by GitHub Actions)
# -------------------------------------------------------------------
REDDIT_CLIENT_ID = os.environ["REDDIT_CLIENT_ID"]
REDDIT_CLIENT_SECRET = os.environ["REDDIT_CLIENT_SECRET"]
REDDIT_USER_AGENT = os.environ["REDDIT_USER_AGENT"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
YOUTUBE_CLIENT_SECRET_JSON = os.environ["YOUTUBE_CLIENT_SECRET_JSON"]
YOUTUBE_TOKEN_JSON = os.environ.get("YOUTUBE_TOKEN", None)

# -------------------------------------------------------------------
# 2. Fetch top Reddit stories from r/NoSleep
# -------------------------------------------------------------------
def fetch_stories():
    reddit = praw.Reddit(client_id=REDDIT_CLIENT_ID,
                         client_secret=REDDIT_CLIENT_SECRET,
                         user_agent=REDDIT_USER_AGENT)
    subreddit = reddit.subreddit("NoSleep")
    stories = []
    for post in subreddit.top(time_filter="week", limit=10):
        if post.ups >= 500 and len(post.selftext) > 2000:
            stories.append({"title": post.title, "text": post.selftext, "id": post.id})
    return stories

# -------------------------------------------------------------------
# 3. Rate story with Gemini
# -------------------------------------------------------------------
def rate_story(story_text):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = "Rate the following horror story on a scale 1-10 for scariness, narrative quality, and US YouTube audience appeal. Only reply with a number."
    response = model.generate_content(prompt + "\n\n" + story_text[:3000])
    try:
        return int(response.text.strip()[0])
    except:
        return 5

# -------------------------------------------------------------------
# 4. Generate script
# -------------------------------------------------------------------
def generate_script(story_text):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-pro')
    prompt = f"""You are a professional horror storyteller. Convert this Reddit story into an 8-10 minute video script. 
    Start with a gripping hook. Use American conversational English. Insert [PAUSE] where the narrator should pause for drama.
    At the end add: "Subscribe before you turn off the lights." 
    Below the script, list 5 visual scene descriptions (one per line) for images to show. Format: "SCENE: description".
    Story: {story_text}"""
    response = model.generate_content(prompt)
    content = response.text
    # Separate script and scenes
    parts = content.split("SCENE:")
    script = parts[0].strip()
    scenes = []
    for line in parts[1:]:
        scenes.append(line.strip())
    return script, scenes

# -------------------------------------------------------------------
# 5. Generate voiceover (Edge‑TTS) + word timings
# -------------------------------------------------------------------
async def generate_audio(script, output_audio="voiceover.mp3", timings_file="word_timings.json"):
    communicate = edge_tts.Communicate(script, "en-US-EricNeural")
    await communicate.save(output_audio)
    # Save word timings for captions later
    with open(timings_file, "w", encoding="utf-8") as f:
        f.write(await communicate.get_word_by_word_data())
    print("Voiceover and word timings saved.")
    return output_audio, timings_file

# -------------------------------------------------------------------
# 6. Create a simple thumbnail using Pillow
# -------------------------------------------------------------------
def create_thumbnail(title):
    img = Image.new('RGB', (1280, 720), color=(20, 5, 5))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 60)
    except:
        font = ImageFont.load_default()
    lines = title.split(" ")
    max_chars = 15
    wrapped = textwrap.wrap(title, width=max_chars)
    y = 200
    for line in wrapped:
        w = draw.textbbox((0,0), line, font=font)[2]
        x = (1280 - w)/2
        draw.text((x, y), line, font=font, fill=(255, 50, 50))
        y += 80
    img.save("thumbnail.jpg")
    return "thumbnail.jpg"

# -------------------------------------------------------------------
# 7. Assemble video with FFmpeg (creepy background + voice + optional subtitles)
# -------------------------------------------------------------------
def assemble_video(voice_path, bg_path="background.mp4", subtitles=None, output="final.mp4"):
    # Get voice duration
    cmd = f'ffprobe -v error -show_entries format=duration -of json "{voice_path}"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    duration = float(json.loads(result.stdout)["format"]["duration"])

    if subtitles and os.path.exists(subtitles):
        sub_path = subtitles.replace("\\", "/")
        filter_chain = f"subtitles='{sub_path}'"
        subprocess.run(
            f'ffmpeg -stream_loop -1 -i "{bg_path}" -i "{voice_path}" '
            f'-c:v libx264 -c:a aac -map 0:v -map 1:a '
            f'-t {duration} -shortest -vf "{filter_chain}" -y "{output}"',
            shell=True
        )
    else:
        subprocess.run(
            f'ffmpeg -stream_loop -1 -i "{bg_path}" -i "{voice_path}" '
            f'-c:v libx264 -c:a aac -map 0:v -map 1:a '
            f'-t {duration} -shortest -y "{output}"',
            shell=True
        )
    return output

# -------------------------------------------------------------------
# 8. Create ASS subtitle file from word timings
# -------------------------------------------------------------------
def create_ass_subtitles(timing_file, output_ass="captions.ass"):
    with open(timing_file, "r", encoding="utf-8") as f:
        words = json.load(f)

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nTitle: Subtitles\nScriptType: v4.00+\n\n")
        f.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
                "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
                "MarginV, Encoding\n")
        f.write("Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
                "1,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1\n\n")
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

        for w in words:
            start = w["offset"]
            end = start + w["duration"]
            def fmt(ms):
                s, ms = divmod(int(ms), 1000)
                m, s = divmod(s, 60)
                h, m = divmod(m, 60)
                return f"{h}:{m:02d}:{s:02d}.{ms*10:02d}"
            f.write(f"Dialogue: 0,{fmt(start)},{fmt(end)},Default,,0,0,0,,{w['word']}\n")
    return output_ass

# -------------------------------------------------------------------
# 9. Upload to YouTube using Data API v3
# -------------------------------------------------------------------
def upload_to_youtube(video_file, title, description, tags, privacy="public"):
    creds_data = json.loads(YOUTUBE_CLIENT_SECRET_JSON)["installed"]
    creds = None
    if YOUTUBE_TOKEN_JSON:
        creds = Credentials.from_authorized_user_info(json.loads(YOUTUBE_TOKEN_JSON))
    else:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_config(
            {"installed": creds_data},
            ["https://www.googleapis.com/auth/youtube.upload"])
        creds = flow.run_local_server(port=0)
        print("YOUTUBE_TOKEN=", creds.to_json())
        raise SystemExit("Token generated, add it to GitHub secrets and re-run.")

    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "24"
        },
        "status": {
            "privacyStatus": privacy
        }
    }
    media = MediaFileUpload(video_file, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    print(f"Uploaded: https://youtu.be/{response['id']}")
    return response

# -------------------------------------------------------------------
# Main orchestration
# -------------------------------------------------------------------
async def main():
    print("Fetching stories...")
    stories = fetch_stories()
    if not stories:
        print("No suitable stories found.")
        return
    best = None
    best_score = 0
    for s in stories:
        score = rate_story(s["text"])
        if score > best_score and score >= 7:
            best_score = score
            best = s
    if not best:
        print("No story scored high enough.")
        return
    print(f"Selected: {best['title']}")

    print("Generating script...")
    script, scenes = generate_script(best["text"])
    print("Script ready.")

    print("Generating voiceover and word timings...")
    voice_path, timings_path = await generate_audio(script)

    print("Creating thumbnail...")
    thumb_path = create_thumbnail(best["title"])

    # Create subtitles
    print("Creating subtitles...")
    subtitle_file = create_ass_subtitles(timings_path)

    # Assemble video with background (you need to have background.mp4 in folder)
    bg_path = "background.mp4"
    if not os.path.exists(bg_path):
        # Fallback: create a black background clip if you don't have one
        subprocess.run(
            'ffmpeg -f lavfi -i color=c=black:s=1280x720:d=5 -c:v libx264 black_temp.mp4', shell=True)
        bg_path = "black_temp.mp4"
    print("Assembling video...")
    video_path = assemble_video(voice_path, bg_path, subtitles=subtitle_file)

    print("Uploading to YouTube...")
    title = best["title"] + " | True Scary Story"
    description = f"Original story by Reddit user.\n\nSubscribe for daily nightmares.\n\n{best['text'][:200]}..."
    tags = ["scary stories", "true horror", "reddit stories", "scary narration", "nightmare dispatch"]
    try:
        upload_to_youtube(video_path, title, description, tags, privacy="public")
        print("Done!")
    except SystemExit:
        pass

if __name__ == "__main__":
    asyncio.run(main())
