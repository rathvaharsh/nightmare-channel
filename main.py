import os, json, subprocess, requests, textwrap
import google.genai as genai
import edge_tts
import asyncio
from PIL import Image, ImageDraw, ImageFont
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
YOUTUBE_CLIENT_SECRET_JSON = os.environ["YOUTUBE_CLIENT_SECRET_JSON"]
YOUTUBE_TOKEN_JSON = os.environ.get("YOUTUBE_TOKEN", None)

def fetch_stories():
    headers = {
        "User-Agent": "NightmareBot/1.0 by Safe-Organization343"
    }
    url = "https://api.pullpush.io/reddit/search/submission/?subreddit=nosleep&sort=desc&sort_type=score&size=10"
    try:
        response = requests.get(url, headers=headers, timeout=30)
        data = response.json()
        stories = []
        for post in data["data"]:
            if post.get("score", 0) >= 500 and len(post.get("selftext", "")) > 2000:
                stories.append({
                    "title": post["title"],
                    "text": post["selftext"],
                    "id": post["id"]
                })
        return stories
    except Exception as e:
        print(f"Error fetching stories: {e}")
        return []

def generate_script(story_text):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""You are a professional horror storyteller. Convert this Reddit story into an 8-10 minute video script. 
    Start with a gripping hook. Use American conversational English. Insert [PAUSE] where the narrator should pause for drama.
    At the end add: "Subscribe before you turn off the lights." 
    Below the script, list 5 visual scene descriptions (one per line) for images to show. Format: "SCENE: description".
    Story: {story_text}"""
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    content = response.text
    parts = content.split("SCENE:")
    script = parts[0].strip()
    scenes = []
    for line in parts[1:]:
        scenes.append(line.strip())
    return script, scenes

async def generate_audio(script, output_audio="voiceover.mp3"):
    communicate = edge_tts.Communicate(script, "en-US-EricNeural")
    await communicate.save(output_audio)
    print("Voiceover saved.")
    return output_audio

def create_thumbnail(title):
    img = Image.new('RGB', (1280, 720), color=(20, 5, 5))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 60)
    except:
        font = ImageFont.load_default()
    wrapped = textwrap.wrap(title, width=15)
    y = 200
    for line in wrapped:
        w = draw.textbbox((0,0), line, font=font)[2]
        x = (1280 - w)/2
        draw.text((x, y), line, font=font, fill=(255, 50, 50))
        y += 80
    img.save("thumbnail.jpg")
    return "thumbnail.jpg"

def assemble_video(voice_path, thumbnail_path):
    if not os.path.exists("black.mp4"):
        subprocess.run("ffmpeg -f lavfi -i color=c=black:s=1280x720:d=600 -c:v libx264 black.mp4", shell=True)
    subprocess.run(f"ffmpeg -i black.mp4 -i {voice_path} -c:v copy -c:a aac -map 0:v -map 1:a -shortest final.mp4", shell=True)
    return "final.mp4"

def upload_to_youtube(video_file, title, description, tags, privacy="public"):
    creds = None
    if YOUTUBE_TOKEN_JSON:
        creds = Credentials.from_authorized_user_info(json.loads(YOUTUBE_TOKEN_JSON))
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

async def main():
    print("Fetching stories...")
    stories = fetch_stories()
    if not stories:
        print("No suitable stories found.")
        return
    best = stories[0]
    print(f"Selected: {best['title']}")
    print("Generating script...")
    script, scenes = generate_script(best["text"])
    print("Generating voiceover...")
    voice_path = "voiceover.mp3"
    await generate_audio(script[:4000], voice_path)
    print("Creating thumbnail...")
    thumb_path = create_thumbnail(best["title"])
    print("Assembling video...")
    video_path = assemble_video(voice_path, thumb_path)
    print("Uploading to YouTube...")
    title = best["title"] + " | True Scary Story"
    description = f"Original story by Reddit user.\n\nSubscribe for daily nightmares.\n\n{best['text'][:200]}..."
    tags = ["scary stories", "true horror", "reddit stories", "scary narration", "nightmare dispatch"]
    upload_to_youtube(video_path, title, description, tags, privacy="public")
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
