import re
import os

handlers_path = "bots/telegram_bot/handlers.py"
try:
    with open(handlers_path, "r") as f:
        content = f.read()

    # Remove command function definitions
    funcs_to_remove = ["cmd_clusters", "cmd_wallet_cluster", "cmd_bot_stats", "cmd_entity", "cmd_entity_lookup"]
    for func in funcs_to_remove:
        # Match from `async def cmd_...` until the line before the next `async def`
        pattern = re.compile(rf"^async def {func}\(.+?(?=^async def |\Z)", re.MULTILINE | re.DOTALL)
        content = pattern.sub("", content)

    # Remove command registrations
    content = re.sub(r'^\s*app\.add_handler\(CommandHandler\("(clusters|wallet_cluster|bot_stats|entity|entity_lookup)".*\n', "", content, flags=re.MULTILINE)

    # Remove from help text
    content = re.sub(r'^\s*"/clusters \[chain.*?\n', "", content, flags=re.MULTILINE)
    content = re.sub(r'^\s*"/wallet_cluster <address>.*?\n', "", content, flags=re.MULTILINE)
    content = re.sub(r'^\s*"/entity <name_or_address>.*?\n', "", content, flags=re.MULTILINE)
    content = re.sub(r'^\s*"/entity_lookup <address>.*?\n', "", content, flags=re.MULTILINE)
    content = re.sub(r'^\s*"/bot_stats —.*?\n', "", content, flags=re.MULTILINE)

    with open(handlers_path, "w") as f:
        f.write(content)
except Exception as e:
    print(e)
    
# Delete Discord command files
def rm_safe(path):
    if os.path.exists(path):
        os.remove(path)

rm_safe("bots/discord_bot/cmd_clusters.py")
rm_safe("bots/discord_bot/cmd_cross_chain.py")
rm_safe("bots/discord_bot/cmd_bot_stats.py")

print("Cleanup complete")
