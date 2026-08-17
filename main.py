import json
import os
import subprocess
import discord
from discord import app_commands


def loadenv(envpath: str) -> dict[str, str]:
    d = {}
    with open(envpath, "r") as env:
        for i in env.read().strip().split("\n"):
            print(i.split("="))
            k, v = i.split("=")
            d[k] = v
    return d


ENV = loadenv(".env")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

DISCORD_TOKEN = ENV.get("DISCORD_TOKEN", None)
if DISCORD_TOKEN is None:
    print("DISCORD_TOKEN not found in .env file")
    exit(1)

DISCORD_GUILD_ID = ENV.get("DISCORD_GUILD_ID", None)
if DISCORD_GUILD_ID is None:
    print("DISCORD_GUILD_ID not found in .env file")
    exit(1)
DISCORD_GUILD_ID = int(DISCORD_GUILD_ID)


DIRECTORY_PATH = ENV.get("DIRECTORY_PATH", None)
if DIRECTORY_PATH is None:
    print("DIRECTORY_PATH not found in .env file")
    exit(1)

CLAUDE_API_KEY = ENV.get("CLAUDE_API_KEY", "none")
if CLAUDE_API_KEY == "none":
    print("CLAUDE_API_KEY not found in .env file")
    exit(1)


# returns (is_error, response)
def get_response(dir: str, question: str) -> tuple[bool, str]:
    custom_env = os.environ | {"ANTHROPIC_API_KEY": CLAUDE_API_KEY}

    result = subprocess.run(
        [
            "claude", "-p", question,
            "--allowedTools", "Read,Grep,Glob,Bash(tree:*),Bash(git log:*),Bash(git blame:*),Bash(git diff:*)",
            "--disallowedTools", "Edit,Write,MultiEdit,NotebookEdit,Bash",
            "--output-format", "json",
            "--system-prompt", "You are a codebase explainer for a Discord bot."
            + " Users ask questions about this repository and you answer using the Read, Grep, and Glob tools to inspect it."
            + " Explain clearly and directly. You have no ability to edit, run, or fix anything, so never offer to."
            + " Do not end responses with questions or suggestions for further action, just answer.",
        ],
        cwd=dir,
        capture_output=True,
        text=True,
        env=custom_env,
    )

    print("GOT CLAUDE RESPONSE:", result.stdout)
    if result.returncode != 0:
        print("CLAUDE STDERR:", result.stderr)
        return True, "claude command failed: " + result.stderr.strip()

    js = json.loads(result.stdout)
    if js["is_error"]:
        return True, js["result"]

    return False, js["result"]


def chunk_message(text: str, limit: int = 2000) -> list[str]:
    chunks = []
    lines: list[str] = []
    length = 0
    in_code_block = False
    fence_lang = ""

    def flush():
        nonlocal lines, length
        if not lines:
            return
        chunk = "\n".join(lines)
        if in_code_block:
            chunk += "\n```"
        chunks.append(chunk)
        lines = []
        length = 0

    for raw_line in text.split("\n"):
        remaining = raw_line
        while True:
            is_fence = remaining.strip().startswith("```")
            reopen_len = len(f"```{fence_lang}\n") if in_code_block else 0
            close_len = 4 if in_code_block else 0  # "\n```"
            budget = limit - reopen_len - close_len

            # a single line too long to ever fit on its own line: hard-split it
            if len(remaining) > budget:
                piece, remaining = remaining[:budget], remaining[budget:]
                more_to_come = True
            else:
                piece, remaining = remaining, ""
                more_to_come = False

            piece_len = len(piece) + 1  # + newline
            if length + piece_len + close_len > limit and lines:
                flush()
                if in_code_block:
                    lines.append(f"```{fence_lang}")
                    length = len(lines[0]) + 1

            lines.append(piece)
            length += piece_len

            if not more_to_come:
                break

        if is_fence:
            if not in_code_block:
                in_code_block = True
                fence_lang = raw_line.strip()[3:]
            else:
                in_code_block = False
                fence_lang = ""

    flush()
    return chunks


@client.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=DISCORD_GUILD_ID))
    print(f"We have logged in as {client.user}")


@tree.command(
    name="ask",
    description="Ask claude code a question about the codebase",
    guild=discord.Object(id=DISCORD_GUILD_ID),
)
async def ask_question_command(interaction, question: str):
    print("Got question:", question)
    try:
        await interaction.response.defer(ephemeral=False, thinking=True)
        is_err, resp = get_response(DIRECTORY_PATH, question)
        if is_err:
            resp = "Error: " + resp

        full_resp = f"**Q:** {question}\n\n{resp}"

        for chunk in chunk_message(full_resp):
            await interaction.followup.send(chunk)
    except Exception as e:
        print("Hard error: ", e)
        await interaction.followup.send("Hard error: " + str(e))


@tree.command(
    name="help",
    description="Show available commands",
    guild=discord.Object(id=DISCORD_GUILD_ID),
)
async def help_command(interaction):
    resp = (
        "**ClaudeRead commands**\n"
        "`/ask <question>` — Ask Claude a question about the codebase. "
        "It can read files and search the repo, but can't edit or run anything.\n"
        "`/help` — Show this message."
    )
    await interaction.response.send_message(resp)


client.run(DISCORD_TOKEN)
