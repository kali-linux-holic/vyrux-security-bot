# Setup Guide

## 1. Discord Developer Portal
1. https://discord.com/developers/applications par jao, naya application banao.
2. "Bot" tab me jao, bot create karo, TOKEN copy kar lo.
3. Wahin par "Privileged Gateway Intents" me ye 3 ON karo:
   - PRESENCE INTENT
   - SERVER MEMBERS INTENT
   - MESSAGE CONTENT INTENT
4. "OAuth2 > URL Generator" me scopes: `bot`, `applications.commands` select karo,
   aur permissions me: Manage Messages, Manage Roles, Moderate Members (timeout),
   Manage Channels, Send Messages, Read Message History, Add Reactions select karo.
   Jo URL bane, usse bot ko apne server me invite karo.

## 2. Bot ka role
Bot ko jitna upar role doge, utne members ke against moderation (word filter,
link filter, spam timeout) kaam karega. Jis member ka role bot se upar hoga,
uske against bot kuch nahi karega — ye automatically code me check hota hai.

## 3. Files
- `bot.py` — pura bot code
- `requirements.txt` — required python packages
- `.env` — isme apna token daalna hai (khud banao):
  ```
  TOKEN=your_bot_token_here
  ```

## 4. Run karna
```
pip install -r requirements.txt
python bot.py
```

## 5. Commands list
- `!ticket` — ticket panel bhejta hai (sirf Manage Server permission wale)
- `!giveaway <time> <prize>` — e.g. `!giveaway 1m Discord Nitro`
- `$afk <reason>` — AFK set karta hai
- `-mi` ya `-mi @member` — message stats (today + lifetime)
- `-vi` ya `-vi @member` — voice time stats (today + lifetime)

## 6. Image links
`bot.py` me top par `AFK_IMAGE`, `GIVEAWAY_IMAGE`, `TICKET_IMAGE` variables hain —
inko apni pasand ki image URLs se replace kar dena for better look.
