<div align="center">

# 📇 TNTU Schedule Bot

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/aiogram-3.x-24A1DE?logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![SQLite](https://img.shields.io/badge/SQLite-Enabled-90D4F4?logo=sqlite&logoColor=white)](https://sqlite.org/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue?logo=gplv3&logoColor=white.svg)](https://www.gnu.org/licenses/gpl-3.0)

**A Telegram bot for tracking Ternopil National Technical University (TNTU) class schedules.**<br>
Provides real-time access to schedules, sends reminders, and allows customizable notifications.

</div>

---

## ✨ Features

- 📅 **View Today's Schedule** - Display the current day's classes at any time.
- 🌙 **Evening Schedule Delivery** - Automatically send tomorrow's schedule every evening at 20:00.
- ⏰ **10-Minute Reminders** - Get notified 10 minutes before each class starts.
- 🔔 **Schedule Change Detection** - Get notified when your group's schedule is updated on the website.
- ⚙️ **Customizable Settings** - Toggle notifications and pause alerts as needed.
- 🔄 **Group Management** - Easily switch between different study groups.
- 📄 **PDF Support** - Direct links to official PDF schedules when available.

## 🛠️ Tech Stack & Data Sources
- **Framework:** [aiogram 3.x](https://docs.aiogram.dev/) (Asynchronous Telegram Bot API)
- **Database:** aiosqlite (Local `users.sqlite3` for preferences and selected groups)
- **Scheduling:** APScheduler (For evening deliveries and pre-class reminders)
- **Data Source:** Web scraping the official [TNTU Website](https://tntu.edu.ua/) using `beautifulsoup4` and `aiohttp`.

## 🚀 Installation & Setup

You can run this bot locally or via Docker. In both cases, you will need a Telegram Bot Token from [@BotFather](https://t.me/BotFather).

### 1. Configuration (`.env`)
First, clone the repository and set up your environment variables:

```bash
git clone https://github.com/Andromedov/TNTU-Telegram-Schedule.git
cd TNTU-Telegram-Schedule
cp .env.example .env
```

Edit `.env` with your Bot Token:

```env
BOT_TOKEN=your_telegram_bot_token_here
```

### 2. Running with Docker (recommended)

The easiest way to run this bot is via Docker Compose:
```bash
docker compose up -d
```

### 3. Running Locally

If you prefer to run it without Docker, ensure you have Python 3.12+ installed.

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the bot
python src/main.py
```

## 📁 Project Structure

```text
TNTU-Telegram-Schedule/
├── src/
│   ├── main.py              # Application entry point
│   ├── handlers.py          # User interactions and commands
│   ├── scraper.py           # TNTU website scraping logic
│   ├── scheduler.py         # Automated tasks and reminders
│   ├── database.py          # SQLite database operations
│   └── messages.json        # Localization and UI text
├── data/                    # Automatically generated (DB & caches)
├── .env.example             # Environment variables template
├── docker-compose.yml       
└── Dockerfile
```

## 📝 Localization
The bot uses a JSON-based localization system. You can modify button labels, user-facing messages, and notification templates by editing `src/messages.json`.

## 📜 License

This project is licensed under the GNU GPL v3 - see the [LICENSE](LICENSE) file for details.

---

**Note:** This bot requires internet access to fetch schedule data from the official TNTU website. Schedule availability and formatting strictly depend on the official website's structure.