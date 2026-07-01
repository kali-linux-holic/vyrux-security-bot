"""
Full-feature Discord Moderation + Utility Bot
-----------------------------------------------
Features:
 1) Bad-word filter (nuke, raid) -> warning reply (skipped for members whose top role >= bot's top role)
 2) Link filter -> deletes non-gif links, pings the member, warns (skipped for members whose top role >= bot's top role)
 3) AFK system: $afk <reason> -> embed, auto-remove afk when they speak again, notifies people who ping an afk user
 4) Reaction giveaway with embed + image
 5) Ticket system with embed + close button
 6) Anti-spam: 7 messages in a row (within 8 seconds) -> 1 min timeout + DM + ping in channel (skipped for higher roles)
 7) -mi [@member]  -> message count (lifetime + today)
 8) -vi [@member]  -> voice time (lifetime + today)

SETUP:
 1) pip install -r requirements.txt
 2) Create a file named ".env" next to this script with:
        TOKEN=your_bot_token_here
 3) In Discord Developer Portal -> Bot -> enable these "Privileged Gateway Intents":
        - SERVER MEMBERS INTENT
        - MESSAGE CONTENT INTENT
        - PRESENCE INTENT (optional)
 4) Run:  python bot.py

NOTE: Bot ka apna role server me jitna upar (higher) hoga, utne members ke against
moderation actions (word filter / link filter / spam timeout) kaam karenge.
Jis member ka role bot ke role se upar (higher) hoga, uske against bot kuch nahi karega
(Discord khud bhi aisa karne nahi deta).
"""

import os
import re
import json
import asyncio
import datetime
from collections import defaultdict, deque

import discord
from discord.ext import commands, tasks
from discord import app_commands

# ----------------------------- CONFIG -----------------------------

PREFIX = "!"  # general command prefix (ticket, giveaway, etc.)
DATA_FILE = "data.json"

BAD_WORDS = ["nuke", "raid"]
LINK_REGEX = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
GIF_REGEX = re.compile(r"\.gif(\?\S*)?$|tenor\.com|giphy\.com", re.IGNORECASE)

SPAM_MSG_LIMIT = 7          # messages
SPAM_TIME_WINDOW = 8        # seconds
TIMEOUT_DURATION_MIN = 1    # minutes

EMBED_COLOR = 0x2b2d31
AFK_IMAGE = "https://images.unsplash.com/photo-1419242902214-272b3f66ee7a?w=600"       # sleeping/afk themed
GIVEAWAY_IMAGE = "giveaway.jpg"   # local file
TICKET_IMAGE   = "ticket.jpg"     # local file
GIVEAWAY_EMOJI = "🎉"

# ----------------------------- DATA STORE -----------------------------

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

data = load_data()
# data structure:
# data[guild_id][user_id] = {
#   "msg_lifetime": int, "msg_today": int,
#   "voice_lifetime": int (seconds), "voice_today": int (seconds),
#   "last_reset": "YYYY-MM-DD"
# }

def get_user_entry(guild_id, user_id):
    gid, uid = str(guild_id), str(user_id)
    data.setdefault(gid, {})
    data[gid].setdefault(uid, {
        "msg_lifetime": 0, "msg_today": 0,
        "voice_lifetime": 0, "voice_today": 0,
        "last_reset": datetime.date.today().isoformat()
    })
    entry = data[gid][uid]
    today = datetime.date.today().isoformat()
    if entry.get("last_reset") != today:
        entry["msg_today"] = 0
        entry["voice_today"] = 0
        entry["last_reset"] = today
    return entry

# ----------------------------- BOT SETUP -----------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

afk_users = {}          # {(guild_id, user_id): reason}
spam_tracker = defaultdict(lambda: deque())   # {(guild_id, user_id): deque[timestamps]}
voice_join_time = {}    # {(guild_id, user_id): datetime}

def is_protected(member: discord.Member) -> bool:
    """True if this member should be SKIPPED by moderation (their role >= bot's top role)."""
    me = member.guild.me
    if member == member.guild.owner:
        return True
    return member.top_role >= me.top_role

def fmt_seconds(total_seconds: int) -> str:
    h, rem = divmod(int(total_seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

# ----------------------------- EVENTS -----------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")

    activity = discord.CustomActivity(name="Don't test my algorithms cuz my developer is vecna.ly2")
    await bot.change_presence(status=discord.Status.dnd, activity=activity)

    if not daily_reset_loop.is_running():
        daily_reset_loop.start()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print("Slash sync failed:", e)


@tasks.loop(hours=24)
async def daily_reset_loop():
    today = datetime.date.today().isoformat()
    for gid, users in data.items():
        for uid, entry in users.items():
            if entry.get("last_reset") != today:
                entry["msg_today"] = 0
                entry["voice_today"] = 0
                entry["last_reset"] = today
    save_data()


@bot.event
async def on_voice_state_update(member, before, after):
    key = (member.guild.id, member.id)
    # joined a voice channel
    if before.channel is None and after.channel is not None:
        voice_join_time[key] = datetime.datetime.utcnow()
    # left voice entirely
    elif before.channel is not None and after.channel is None:
        joined = voice_join_time.pop(key, None)
        if joined:
            spent = (datetime.datetime.utcnow() - joined).total_seconds()
            entry = get_user_entry(member.guild.id, member.id)
            entry["voice_lifetime"] += int(spent)
            entry["voice_today"] += int(spent)
            save_data()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    member: discord.Member = message.author

    # ---------- AFK auto remove ----------
    key = (message.guild.id, member.id)
    if key in afk_users:
        del afk_users[key]
        try:
            await message.channel.send(
                f"👋 Welcome back {member.mention}, I have removed your AFK status!",
                delete_after=8
            )
        except discord.Forbidden:
            pass

    # ---------- Notify if someone pings an AFK user ----------
    if message.mentions:
        for u in message.mentions:
            k2 = (message.guild.id, u.id)
            if k2 in afk_users:
                embed = discord.Embed(
                    description=f"💤 **{u.display_name}** is currently AFK: {afk_users[k2]}",
                    color=EMBED_COLOR
                )
                await message.channel.send(embed=embed)

    protected = is_protected(member)

    # ---------- Bad word filter ----------
    content_lower = message.content.lower()
    if not protected:
        for word in BAD_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", content_lower):
                await message.reply(
                    f"⚠️ {member.mention}, please dont use these types of words in server."
                )
                break

    # ---------- Link filter (allow gif only) ----------
    if not protected and LINK_REGEX.search(message.content):
        if not GIF_REGEX.search(message.content):
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
            warn = await message.channel.send(
                f"🔗 {member.mention}, links are not allowed in server. Only GIF links are permitted."
            )
            await warn.delete(delay=8)
            return  # don't process further / don't count as a normal message for spam

    # ---------- Spam tracker ----------
    if not protected:
        dq = spam_tracker[key]
        now = datetime.datetime.utcnow().timestamp()
        dq.append(now)
        while dq and now - dq[0] > SPAM_TIME_WINDOW:
            dq.popleft()
        if len(dq) >= SPAM_MSG_LIMIT:
            dq.clear()
            try:
                until = discord.utils.utcnow() + datetime.timedelta(minutes=TIMEOUT_DURATION_MIN)
                await member.timeout(until, reason="Spamming")
                await message.channel.send(
                    f"⏱️ {member.mention}, you got timeout in **{message.guild.name}** for spamming."
                )
                try:
                    await member.send(
                        f"⏱️ You got timeout in **{message.guild.name}** for spamming."
                    )
                except discord.Forbidden:
                    pass
            except discord.Forbidden:
                pass

    # ---------- Message count tracking ----------
    entry = get_user_entry(message.guild.id, member.id)
    entry["msg_lifetime"] += 1
    entry["msg_today"] += 1
    save_data()

    await bot.process_commands(message)


# ----------------------------- AFK COMMAND -----------------------------

# ----------------------------- -mi / -vi COMMANDS -----------------------------
# (these use a different prefix character, handled manually)

@bot.event
async def on_message_edit(before, after):
    pass


@bot.listen("on_message")
async def custom_prefix_commands(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    content = message.content.strip()

    if content.startswith("$afk"):
        reason = content[len("$afk"):].strip() or "AFK"
        key = (message.guild.id, message.author.id)
        afk_users[key] = reason
        embed = discord.Embed(
            title="💤 You are now AFK",
            description=f"**Reason:** {reason}\nMembers who mention you will be notified.",
            color=EMBED_COLOR
        )
        embed.set_thumbnail(url=AFK_IMAGE)
        embed.set_footer(text=f"{message.author.display_name}", icon_url=message.author.display_avatar.url)
        await message.channel.send(embed=embed)
        return

    if content.startswith("-mi"):
        target = message.mentions[0] if message.mentions else message.author
        entry = get_user_entry(message.guild.id, target.id)
        embed = discord.Embed(title=f"📊 Message Stats — {target.display_name}", color=EMBED_COLOR)
        embed.add_field(name="Today", value=str(entry["msg_today"]), inline=True)
        embed.add_field(name="Lifetime", value=str(entry["msg_lifetime"]), inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)
        await message.channel.send(embed=embed)

    elif content.startswith("-vi"):
        target = message.mentions[0] if message.mentions else message.author
        entry = get_user_entry(message.guild.id, target.id)

        # include current ongoing voice session if user is in a call right now
        live_extra = 0
        key = (message.guild.id, target.id)
        if key in voice_join_time:
            live_extra = int((datetime.datetime.utcnow() - voice_join_time[key]).total_seconds())

        embed = discord.Embed(title=f"🎙️ Voice Stats — {target.display_name}", color=EMBED_COLOR)
        embed.add_field(name="Today", value=fmt_seconds(entry["voice_today"] + live_extra), inline=True)
        embed.add_field(name="Lifetime", value=fmt_seconds(entry["voice_lifetime"] + live_extra), inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)
        await message.channel.send(embed=embed)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ You need the **Manage Server** permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing argument: `{error.param.name}`. Usage example: `!giveaway 1m Discord Nitro`")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        await ctx.send(f"⚠️ Error: {error}")
        print("Command error:", error)


# ----------------------------- TICKET SYSTEM -----------------------------

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...", ephemeral=False)
        await asyncio.sleep(5)
        await interaction.channel.delete()


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.success, emoji="🎫", custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="Tickets")
        if category is None:
            category = await guild.create_category("Tickets")

        existing = discord.utils.get(
            guild.text_channels,
            name=f"ticket-{interaction.user.name}".lower().replace(" ", "-")
        )
        if existing:
            await interaction.followup.send(f"You already have an open ticket: {existing.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        channel = await guild.create_text_channel(
            f"ticket-{interaction.user.name}".lower().replace(" ", "-"),
            category=category,
            overwrites=overwrites
        )

        embed = discord.Embed(
            title="🎫 Support Ticket",
            description=f"Hello {interaction.user.mention}, thanks for reaching out!\nPlease describe your issue and a staff member will be with you shortly.",
            color=EMBED_COLOR
        )
        ticket_file = discord.File(TICKET_IMAGE, filename="ticket.jpg")
        embed.set_image(url="attachment://ticket.jpg")

        await channel.send(
            content=f"{interaction.user.mention} your ticket has been created!",
            embed=embed,
            file=ticket_file,
            view=TicketCloseView()
        )
        await interaction.followup.send(f"✅ Ticket created: {channel.mention}", ephemeral=True)


@bot.command(name="ticket")
@commands.has_permissions(manage_guild=True)
async def ticket_panel(ctx):
    embed = discord.Embed(
        title="🎫 Need Help?",
        description="Click the button below to open a private support ticket.",
        color=EMBED_COLOR
    )
    embed.set_image(url="attachment://ticket.jpg")
    ticket_file = discord.File(TICKET_IMAGE, filename="ticket.jpg")
    await ctx.send(embed=embed, view=TicketOpenView(), file=ticket_file)


class InfiniteGiveawayView(discord.ui.View):
    def __init__(self, prize: str, host_id: int, winner_count: int = 1):
        super().__init__(timeout=None)
        self.prize = prize
        self.host_id = host_id
        self.winner_count = winner_count

    @discord.ui.button(label="End Giveaway", style=discord.ButtonStyle.danger, emoji="🛑", custom_id="end_giveaway")
    async def end_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("⛔ You need the **Manage Server** permission to end this giveaway.", ephemeral=True)
            return

        message = interaction.message
        reaction = discord.utils.get(message.reactions, emoji=GIVEAWAY_EMOJI)
        users = [u async for u in reaction.users() if not u.bot] if reaction else []

        button.disabled = True
        await interaction.response.edit_message(view=self)

        if not users:
            result_embed = discord.Embed(
                title="🎉 Giveaway Ended",
                description=f"No valid entries, no winner for **{self.prize}**.",
                color=EMBED_COLOR
            )
            await interaction.channel.send(embed=result_embed)
            return

        import random
        chosen = random.sample(users, min(self.winner_count, len(users)))
        mentions = ", ".join(w.mention for w in chosen)
        result_embed = discord.Embed(
            title="🎉 Giveaway Ended",
            description=f"Congratulations {mentions}! You won **{self.prize}**!",
            color=EMBED_COLOR
        )
        result_file = discord.File(GIVEAWAY_IMAGE, filename="giveaway.jpg")
        result_embed.set_image(url="attachment://giveaway.jpg")
        await interaction.channel.send(embed=result_embed, file=result_file)


@bot.tree.command(name="giveaway", description="Start a giveaway.")
@app_commands.describe(
    prize="What are you giving away?",
    winners="Number of winners (default: 1)",
    duration="Duration e.g. 30s, 10m, 2h (leave empty for infinite / end manually)"
)
async def slash_giveaway(interaction: discord.Interaction, prize: str, winners: int = 1, duration: str = None):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("⛔ You need the **Manage Server** permission to use this command.", ephemeral=True)
        return

    seconds = None
    end_text = "This giveaway has **no time limit** — staff will end it manually."
    if duration:
        try:
            seconds = parse_duration(duration)
            end_time = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
            end_text = f"Ends: <t:{int(end_time.timestamp())}:R>"
        except ValueError:
            await interaction.response.send_message("⚠️ Invalid duration format. Use e.g. `30s`, `10m`, `2h`", ephemeral=True)
            return

    embed = discord.Embed(
        title="🎉 GIVEAWAY 🎉",
        description=(
            f"**Prize:** {prize}\n"
            f"**Winners:** {winners}\n"
            f"React with {GIVEAWAY_EMOJI} to enter!\n"
            f"{end_text}"
        ),
        color=EMBED_COLOR
    )
    embed.set_image(url="attachment://giveaway.jpg")
    embed.set_footer(text=f"Hosted by {interaction.user.display_name}")

    view = InfiniteGiveawayView(prize=prize, host_id=interaction.user.id, winner_count=winners)
    giveaway_file = discord.File(GIVEAWAY_IMAGE, filename="giveaway.jpg")
    await interaction.response.send_message(embed=embed, view=view, file=giveaway_file)
    msg = await interaction.original_response()
    await msg.add_reaction(GIVEAWAY_EMOJI)

    if seconds:
        await asyncio.sleep(seconds)
        msg = await interaction.channel.fetch_message(msg.id)
        reaction = discord.utils.get(msg.reactions, emoji=GIVEAWAY_EMOJI)
        users = [u async for u in reaction.users() if not u.bot] if reaction else []

        if not users:
            result_embed = discord.Embed(
                title="🎉 Giveaway Ended",
                description=f"No valid entries, no winner for **{prize}**.",
                color=EMBED_COLOR
            )
            await interaction.channel.send(embed=result_embed)
            return

        import random
        chosen = random.sample(users, min(winners, len(users)))
        mentions = ", ".join(w.mention for w in chosen)
        result_embed = discord.Embed(
            title="🎉 Giveaway Ended",
            description=f"Congratulations {mentions}! You won **{prize}**!",
            color=EMBED_COLOR
        )
        result_file = discord.File(GIVEAWAY_IMAGE, filename="giveaway.jpg")
        result_embed.set_image(url="attachment://giveaway.jpg")
        await interaction.channel.send(embed=result_embed, file=result_file)

        # disable the End button
        view.children[0].disabled = True
        await msg.edit(view=view)


@bot.tree.command(name="ticket", description="Post a ticket panel so members can open support tickets.")
async def slash_ticket(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("⛔ You need the **Manage Server** permission to use this command.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎫 Need Help?",
        description="Click the button below to open a private support ticket.",
        color=EMBED_COLOR
    )
    embed.set_image(url="attachment://ticket.jpg")
    ticket_file = discord.File(TICKET_IMAGE, filename="ticket.jpg")
    await interaction.response.send_message(embed=embed, view=TicketOpenView(), file=ticket_file)


# ----------------------------- TIMED GIVEAWAY (prefix command) -----------------------------

def parse_duration(s: str) -> int:
    """Parses strings like 30s, 5m, 2h into seconds."""
    match = re.match(r"^(\d+)([smh])$", s.lower())
    if not match:
        raise ValueError("Invalid duration format. Use e.g. 30s, 10m, 1h")
    value, unit = int(match.group(1)), match.group(2)
    return value * {"s": 1, "m": 60, "h": 3600}[unit]


@bot.command(name="giveaway")
@commands.has_permissions(manage_guild=True)
async def giveaway_cmd(ctx, duration: str, *, prize: str):
    try:
        seconds = parse_duration(duration)
    except ValueError as e:
        await ctx.send(str(e))
        return

    end_time = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
    embed = discord.Embed(
        title="🎉 GIVEAWAY 🎉",
        description=f"**Prize:** {prize}\nReact with {GIVEAWAY_EMOJI} to enter!\nEnds: <t:{int(end_time.timestamp())}:R>",
        color=EMBED_COLOR
    )
    embed.set_image(url=GIVEAWAY_IMAGE)
    embed.set_footer(text=f"Hosted by {ctx.author.display_name}")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction(GIVEAWAY_EMOJI)

    await asyncio.sleep(seconds)

    msg = await ctx.channel.fetch_message(msg.id)
    users = [u async for u in msg.reactions[0].users() if not u.bot] if msg.reactions else []

    if not users:
        result_embed = discord.Embed(
            title="🎉 Giveaway Ended",
            description=f"No valid entries, no winner for **{prize}**.",
            color=EMBED_COLOR
        )
        await ctx.send(embed=result_embed)
        return

    import random
    winner = random.choice(users)
    result_embed = discord.Embed(
        title="🎉 Giveaway Ended",
        description=f"Congratulations {winner.mention}! You won **{prize}**!",
        color=EMBED_COLOR
    )
    result_embed.set_image(url=GIVEAWAY_IMAGE)
    await ctx.send(embed=result_embed)


# ----------------------------- RUN -----------------------------

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, format, *args):
        pass

def run_server():
    server = HTTPServer(("0.0.0.0", 8080), KeepAlive)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()

    token = os.getenv("TOKEN")
    if not token:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            token = os.getenv("TOKEN")
        except ImportError:
            pass
    if not token:
        raise RuntimeError("TOKEN not found. Create a .env file with TOKEN=your_bot_token")
    bot.run(token)
