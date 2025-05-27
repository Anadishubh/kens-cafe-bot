import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio
import datetime
from keep_alive import keep_alive

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

keep_alive()

SONG_QUEUES = {}
LOOP_MODES = {}  # none, one, all
CURRENT_SONG = {}
DISCONNECT_TASKS = {}

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="k!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} is online!")

@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()
    user_vc = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        voice_client = await user_vc.connect()
    elif user_vc != voice_client.channel:
        await voice_client.move_to(user_vc)

    ydl_options = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }

    query = "ytsearch1:" + song_query
    results = await search_ytdlp_async(query, ydl_options)
    tracks = results.get("entries", [])

    if not tracks:
        await interaction.followup.send("No results found.")
        return

    track = tracks[0]
    audio_url = track["url"]
    title = track["title"]
    duration = str(datetime.timedelta(seconds=track["duration"]))

    guild_id = str(interaction.guild_id)
    if guild_id not in SONG_QUEUES:
        SONG_QUEUES[guild_id] = deque()
        LOOP_MODES[guild_id] = "none"

    SONG_QUEUES[guild_id].append((audio_url, title, duration))

    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"Added to queue: **{title}** ({duration})")
    else:
        await interaction.followup.send(f"Now playing: **{title}** ({duration})")
        await play_next_song(voice_client, guild_id, interaction.channel)

async def play_next_song(voice_client, guild_id, channel):
    if guild_id not in LOOP_MODES:
        LOOP_MODES[guild_id] = "none"

    loop_mode = LOOP_MODES[guild_id]
    queue = SONG_QUEUES[guild_id]

    if not queue:
        CURRENT_SONG[guild_id] = None
        await start_disconnect_timer(voice_client, guild_id)
        return

    if loop_mode == "one" and CURRENT_SONG.get(guild_id):
        audio_url, title, duration = CURRENT_SONG[guild_id]
    else:
        audio_url, title, duration = queue.popleft()
        if loop_mode == "all":
            queue.append((audio_url, title, duration))
        CURRENT_SONG[guild_id] = (audio_url, title, duration)

    ffmpeg_options = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": "-vn -c:a libopus -b:a 96k"
    }

    source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options, executable="bin\\ffmpeg\\ffmpeg.exe")

    def after_play(error):
        if error:
            print(f"Playback error: {error}")
        fut = play_next_song(voice_client, guild_id, channel)
        asyncio.run_coroutine_threadsafe(fut, bot.loop)

    voice_client.play(source, after=after_play)
    await channel.send(f"Now playing: **{title}** ({duration})")

    # Cancel any disconnect timers
    if guild_id in DISCONNECT_TASKS:
        DISCONNECT_TASKS[guild_id].cancel()
        del DISCONNECT_TASKS[guild_id]

async def start_disconnect_timer(voice_client, guild_id):
    async def disconnect_after_delay():
        await asyncio.sleep(60)
        await voice_client.disconnect()
        SONG_QUEUES[guild_id] = deque()
        CURRENT_SONG[guild_id] = None

    DISCONNECT_TASKS[guild_id] = asyncio.create_task(disconnect_after_delay())

@bot.tree.command(name="queue", description="Display the current song queue.")
async def queue(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())
    if not queue:
        await interaction.response.send_message("Queue is empty.")
        return

    description = ""
    for i, (url, title, duration) in enumerate(queue, 1):
        description += f"**{i}.** {title} ({duration})\n"
    await interaction.response.send_message(f"**Queue:**\n{description}")

@bot.tree.command(name="nowplaying", description="Show the current playing song.")
async def nowplaying(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    song = CURRENT_SONG.get(guild_id)
    if song:
        _, title, duration = song
        await interaction.response.send_message(f"Now playing: **{title}** ({duration})")
    else:
        await interaction.response.send_message("No song is currently playing.")

@bot.tree.command(name="loop", description="Toggle loop mode: none, one, all")
@app_commands.describe(mode="Loop mode: none / one / all")
async def loop(interaction: discord.Interaction, mode: str):
    if mode not in ["none", "one", "all"]:
        await interaction.response.send_message("Invalid loop mode. Use 'none', 'one', or 'all'.")
        return
    LOOP_MODES[str(interaction.guild_id)] = mode
    await interaction.response.send_message(f"Loop mode set to: **{mode}**")

@bot.tree.command(name="pause", description="Pause playback")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("Playback paused.")
    else:
        await interaction.response.send_message("Nothing is playing.")

@bot.tree.command(name="resume", description="Resume playback")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("Playback resumed.")
    else:
        await interaction.response.send_message("Nothing is paused.")

@bot.tree.command(name="stop", description="Stop and clear queue")
async def stop(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    vc = interaction.guild.voice_client
    if vc:
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await vc.disconnect()
    SONG_QUEUES[guild_id] = deque()
    CURRENT_SONG[guild_id] = None
    await interaction.response.send_message("Stopped and disconnected.")

@bot.tree.command(name="skip", description="Skip current song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("Skipped current song.")
    else:
        await interaction.response.send_message("Nothing to skip.")

bot.run(TOKEN)
