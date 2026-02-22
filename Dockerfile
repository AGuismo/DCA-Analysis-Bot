FROM python:3.9-slim

WORKDIR /app

# Install dependencies first (cache layer)
COPY bot_requirements.txt .
RUN pip install --no-cache-dir -r bot_requirements.txt

# Copy only the bot script
COPY discord_bot.py .

CMD ["python3", "discord_bot.py"]
